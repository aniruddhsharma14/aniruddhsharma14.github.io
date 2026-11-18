"""
Stage 0c -- VALIDATION. Says which matches may be trusted, before any analysis.

Checks per match:

 1. STRUCTURE   all three legs present, with rows.
 2. LEG SUM     the three YES legs are mutually exclusive and exhaustive, so they
                must price to 100. Checked three ways, because the mid version has
                a KNOWN structural bias: with a 1-tick spread every mid sits at
                bid+0.5, so three mids sum to about bid_sum + 1.5.
                  - mid sum      : expect ~100.5-101.5, diagnostic only
                  - ASK sum      : cost to buy all three YES. Must be >= 100.
                                   Below 100 is a genuine arbitrage.
                  - BID sum      : proceeds from selling all three. Must be <= 100.
                                   Above 100 is a genuine arbitrage.
                The ask/bid versions are the real test: they use executable prices
                and need no assumption about where fair value sits in the spread.
 3. COVERAGE    fraction of the match window that actually has a two-sided book,
                and the largest gap with no update.
 4. PROVENANCE  did a POPULATED snapshot land before the window opened, and how
                long before. With none, the book was rebuilt from deltas alone:
                near-touch prices converge within minutes but untouched deep levels
                stay invisible, so depth is a lower bound. Recorded, not hidden.
 5. SETTLEMENT  exactly one leg must finish near 100 and the others near 0. This is
                an end-to-end check on the whole chain, including the NO->YES
                inversion: get that wrong and the winner finishes near 0.
 6. INTEGRITY   seq gaps (backward/repeat sequence) and negative-size clamps.
"""
import pandas as pd, numpy as np, glob, os, json

CANON = 'canonical'
# CANON_SUFFIX validates a parallel tree (books_v2/ ...) and writes its own
# manifest/validation files beside the live ones, never over them.
SUF = os.environ.get('CANON_SUFFIX', '')
SUF = '' if SUF == 'orig' else SUF      # 'orig' names the original build, as in canon_load
WIN_SPLIT = 'core'


def leg_last_trades(match):
    """Final traded price per leg. Used for the settlement check instead of the last
    mid: the top-of-book series stops the moment the book goes one-sided, which is
    exactly what happens at settlement when the losing side's book empties. 26JUN12
    CANBIH froze at TIE mid 63.5 while TIE was printing at 99 in the same second."""
    out = {}
    for f in sorted(glob.glob(f'{CANON}/books{SUF}/{match}/*.trades.p*.parquet')) or \
             sorted(glob.glob(f'{CANON}/books{SUF}/{match}/*.trades.parquet')):
        leg = os.path.basename(f).split('__')[0].rsplit('-', 1)[-1]
        d = pd.read_parquet(f, columns=['ts', 'price'])
        if len(d):
            d = d.sort_values('ts')
            out[leg] = (float(d.price.iloc[-1]), int(d.ts.iloc[-1]))
    return out


def leg_frames(match):
    out = {}
    for f in sorted(glob.glob(f'{CANON}/books{SUF}/{match}/*.book.p*.parquet')) or \
             sorted(glob.glob(f'{CANON}/books{SUF}/{match}/*.book.parquet')):
        leg = os.path.basename(f).split('__')[0].rsplit('-', 1)[-1]
        d = pd.read_parquet(f, columns=['ts', 'bid', 'ask', 'mid', 'spread',
                                        'bid_size', 'ask_size'])
        out.setdefault(leg, []).append(d)
    return {k: pd.concat(v).sort_values('ts').reset_index(drop=True) for k, v in out.items()}


def main():
    if not glob.glob(f'{CANON}/_manifest{SUF}/*.csv'):
        raise SystemExit(f'no manifests in {CANON}/_manifest{SUF}/ -- this tree was not '
                         f'built by stage0_reconstruct, so there is nothing to validate')
    man = pd.concat([pd.read_csv(f) for f in glob.glob(f'{CANON}/_manifest{SUF}/*.csv')])
    man.to_csv(f'{CANON}/manifest{SUF}.csv', index=False)
    w = pd.read_csv(f'{CANON}/windows.csv'); w = w[w.covered].set_index('match')

    rows = []
    for match in sorted(os.listdir(f'{CANON}/books{SUF}')):
        legs = leg_frames(match)
        if match not in w.index:
            continue
        t0, t1 = int(w.loc[match, 't0_ms']), int(w.loc[match, 't1_ms'])
        kick, end = int(w.loc[match, 'kickoff_ms']), int(w.loc[match, 'end_ms'])
        mg = man[man.match == match]

        r = dict(match=match, n_legs=len(legs),
                 legs=';'.join(f'{k}:{len(v)}' for k, v in sorted(legs.items())),
                 book_rows=int(sum(len(v) for v in legs.values())),
                 trade_rows=int(mg.trade_rows.sum()),
                 seq_gaps=int(mg.seq_gaps.max()), neg_clamps=int(mg.neg_clamps.max()),
                 pop_snap_before=int(mg.pop_snaps_before_t0.max()),
                 deltas_before=int(mg.deltas_before_t0.max()))

        # --- snapshot recency: how stale was the last populated seed at window open
        ls = mg.last_pop_snap_ms.dropna()
        r['snap_age_h'] = round((t0 - ls.max()) / 3600000, 2) if len(ls) else None

        if legs:
            fr = [(v.spread <= 0).mean() for v in legs.values() if len(v)]
            r['max_leg_crossed'] = round(max(fr), 5) if fr else None
        r['uncross_prunes'] = int(mg.uncross_prunes.max()) if 'uncross_prunes' in mg else None

        if len(legs) == 3 and all(len(v) > 100 for v in legs.values()):
            # align all three legs on the union of timestamps, in-play only
            allts = sorted(set().union(*[set(v.ts) for v in legs.values()]))
            allts = [t for t in allts if kick <= t <= end]
            A = pd.DataFrame(index=allts)
            for k, v in legs.items():
                vv = v[~v.ts.duplicated(keep='last')].set_index('ts')
                for col in ('mid', 'bid', 'ask'):
                    A[f'{k}_{col}'] = vv[col].reindex(allts).ffill()
            A = A.dropna()
            if len(A):
                mids = A[[c for c in A if c.endswith('_mid')]].sum(axis=1)
                asks = A[[c for c in A if c.endswith('_ask')]].sum(axis=1)
                bids = A[[c for c in A if c.endswith('_bid')]].sum(axis=1)
                r.update(n_aligned=len(A),
                         mid_sum_mean=round(mids.mean(), 3),
                         mid_sum_med=round(mids.median(), 2),
                         ask_sum_min=round(asks.min(), 1),
                         ask_sum_med=round(asks.median(), 1),
                         bid_sum_max=round(bids.max(), 1),
                         bid_sum_med=round(bids.median(), 1),
                         # genuine arbitrage fractions, using executable prices only
                         frac_ask_sum_lt100=round((asks < 100).mean(), 5),
                         frac_bid_sum_gt100=round((bids > 100).mean(), 5))

                # --- settlement: exactly one leg near 100, the rest near 0,
                # judged on the last TRADED price (see leg_last_trades)
                lt = leg_last_trades(match)
                fin = {k: v[0] for k, v in lt.items()}
                hi = [k for k, v in fin.items() if v >= 90]
                lo = [k for k, v in fin.items() if v <= 10]
                r.update(final=';'.join(f'{k}={v:.0f}' for k, v in sorted(fin.items())),
                         final_mid=';'.join(f'{k}={legs[k].mid.iloc[-1]:.1f}'
                                            for k in sorted(legs)),
                         settle_ok=(len(hi) == 1 and len(lo) == 2),
                         winner=hi[0] if len(hi) == 1 else None)

        # --- coverage of the in-play window, from the busiest leg
        busiest = max(legs, key=lambda k: len(legs[k])) if legs else None
        if busiest:
            ts = legs[busiest].ts.values
            ts = ts[(ts >= kick) & (ts <= end)]
            if len(ts) > 1:
                gaps = np.diff(ts)
                r.update(inplay_updates=len(ts),
                         inplay_span_min=round((ts[-1] - ts[0]) / 60000, 1),
                         max_gap_s=round(gaps.max() / 1000, 1),
                         median_gap_ms=int(np.median(gaps)),
                         cover_frac=round((ts[-1] - ts[0]) / max(end - kick, 1), 3))
        rows.append(r)

    v = pd.DataFrame(rows)

    # --- QUALITY TIER. Downstream work filters on this instead of re-deciding
    # per experiment what counts as usable data.
    #   A : settles, a populated snapshot seeded the book, no long blackout
    #   B : settles, book rebuilt from deltas alone (depth is a LOWER BOUND), or a
    #       gap up to 2 min
    #   C : settles but has a blackout over 2 min -- usable for price-path work,
    #       not for anything depending on continuous book state
    #   F : fails a structural check -- do not use
    def tier(r):
        if r.n_legs != 3 or r.settle_ok is not True:
            return 'F'
        # a crossed book is impossible, so material crossing means the
        # reconstruction is wrong, not that the market was odd
        if r.max_leg_crossed is not None and r.max_leg_crossed > 0.05:
            return 'F'
        if r.max_leg_crossed is not None and r.max_leg_crossed > 0.005:
            return 'C'
        if r.max_gap_s is not None and r.max_gap_s > 120:
            return 'C'
        if r.pop_snap_before > 0:
            return 'A'
        return 'B'
    v['tier'] = v.apply(tier, axis=1)
    v['depth_is_lower_bound'] = v.pop_snap_before == 0
    v.to_csv(f'{CANON}/validation{SUF}.csv', index=False)

    print(f"=== STAGE 0 VALIDATION: {len(v)} matches ===\n")
    print("STRUCTURE")
    print(f"  all three legs present : {(v.n_legs==3).sum()}/{len(v)}")
    print(f"  settlement check pass  : {int(v.settle_ok.sum())}/{v.settle_ok.notna().sum()}"
          "   (exactly one leg ->100, two ->0)")
    print(f"\nLEG SUM -- mid (structurally biased high by ~1.5, diagnostic only)")
    print(f"  mean of per-match mean : {v.mid_sum_mean.mean():.3f}")
    print(f"  range of medians       : {v.mid_sum_med.min():.2f} .. {v.mid_sum_med.max():.2f}")
    print(f"\nLEG SUM -- EXECUTABLE prices (the real test)")
    print(f"  median ask-sum (buy all three, must be >=100): "
          f"{v.ask_sum_med.median():.1f}  [{v.ask_sum_med.min():.1f} .. {v.ask_sum_med.max():.1f}]")
    print(f"  median bid-sum (sell all three, must be <=100): "
          f"{v.bid_sum_med.median():.1f}  [{v.bid_sum_med.min():.1f} .. {v.bid_sum_med.max():.1f}]")
    print(f"  matches where ask-sum EVER < 100 : {(v.frac_ask_sum_lt100>0).sum()}")
    print(f"  matches where bid-sum EVER > 100 : {(v.frac_bid_sum_gt100>0).sum()}")
    print(f"\nPROVENANCE")
    print(f"  matches WITH a populated snapshot before the window : {(v.pop_snap_before>0).sum()}")
    print(f"  matches seeded from deltas only                     : {(v.pop_snap_before==0).sum()}")
    print(f"  deltas applied before window opens (median)         : {v.deltas_before.median():,.0f}")
    print(f"\nCOVERAGE (in-play)")
    print(f"  median book updates per match : {v.inplay_updates.median():,.0f}")
    print(f"  median inter-update gap       : {v.median_gap_ms.median():.0f} ms")
    print(f"  median largest gap            : {v.max_gap_s.median():.1f} s")
    print(f"  worst largest gap             : {v.max_gap_s.max():.1f} s")
    print(f"\nCROSSED BOOKS (impossible; indicates stale levels after dropped messages)")
    print(f"  matches with zero crossing      : {(v.max_leg_crossed==0).sum()}/{len(v)}")
    print(f"  worst leg crossing fraction     : {v.max_leg_crossed.max():.4%}")
    print(f"  matches above 0.5%              : {(v.max_leg_crossed>0.005).sum()}")
    print(f"  stale levels pruned (median/max): {v.uncross_prunes.median():,.0f} / {v.uncross_prunes.max():,.0f}")
    print(f"\nINTEGRITY -- negative-size clamps split by provenance")
    seeded = v[v.pop_snap_before==0]; snapped = v[v.pop_snap_before>0]
    for nm, grp in (('seeded from deltas only', seeded), ('had a populated snapshot', snapped)):
        if len(grp):
            print(f"  {nm:26s} n={len(grp):2d}  median clamps={grp.neg_clamps.median():>8,.0f}  "
                  f"max={grp.neg_clamps.max():>8,.0f}")
    print(f"\nINTEGRITY")
    print(f"  matches with any seq gap     : {(v.seq_gaps>0).sum()}  (max {v.seq_gaps.max():,})")
    print(f"  matches with negative clamps : {(v.neg_clamps>0).sum()}  (max {v.neg_clamps.max():,})")
    print("\nQUALITY TIERS")
    for t in ('A','B','C','F'):
        g = v[v.tier==t]
        if len(g):
            print(f"  {t}: {len(g):2d} matches, {g.book_rows.sum():>10,} book rows, "
                  f"{g.trade_rows.sum():>9,} trades")
    print(f"\n  usable for book-state work (A+B): {(v.tier.isin(['A','B'])).sum()} matches, "
          f"{v[v.tier.isin(['A','B'])].book_rows.sum():,} book rows")
    print("\nFAILURES / SUSPECTS")
    bad = v[(v.n_legs != 3) | (v.settle_ok != True) | (v.max_gap_s > 120)]
    if len(bad) == 0:
        print("  none")
    else:
        print(bad[['match','settle_ok','final','final_mid','max_gap_s']].to_string(index=False))


if __name__ == '__main__':
    main()
