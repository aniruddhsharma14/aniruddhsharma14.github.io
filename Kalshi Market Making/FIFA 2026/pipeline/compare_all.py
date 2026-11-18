"""
Check EVERY leg against Predexon's independent reconstruction.

This replaces inference with measurement. As of 2026-10-12 only 2 of 27
depth_clean matches had been checked externally; the other 25 were inferred
clean from sharing two properties with the checked one (snapshot applied, zero
sequence gaps). That inference has a mechanism behind it but it is still an
inference, and this script removes the need for it.

It also measures the OTHER two tiers properly, instead of extrapolating the
price_flow_only tier from the single leg (26JUN24RSAKOR-KOR) it was characterised
from.

Each leg is compared against the tree its tier lives in:
    depth_clean      -> canonical/books_v2
    gaps             -> canonical/books_gaps
    price_flow_only  -> canonical/books_priceonly

RESUMABLE. Each leg's result is appended to canonical/state_cmp_all.csv as soon
as it is computed, and legs already in that file are skipped. Snapshots are
cached under canonical/predexon_raw/, so a re-run costs no API requests. Kill it
and restart it freely.

    export PREDEXON_API_KEY=...
    python3 pipeline/compare_all.py
    TIERS=depth_clean python3 pipeline/compare_all.py     # one tier only
    LIMIT=10 python3 pipeline/compare_all.py              # first 10 legs

Expect ~5 s of fetching per leg plus compute; 120 legs is roughly 20-40 minutes.
"""
from __future__ import annotations
import os, sys, glob, gc, csv
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import CANON, windows
from predexon_deltas import get_snaps

TREE = {'depth_clean': '_v2', 'gaps': '_gaps', 'price_flow_only': '_priceonly'}
OUT = os.path.join(CANON, 'state_cmp_all.csv')
DUST = 0.5
BANDS = ((0, 0, 'touch'), (1, 3, 'd1_3'), (4, 10, 'd4_10'),
         (11, 30, 'd11_30'), (31, 200, 'd31up'))
FIELDS = (['match', 'leg', 'tier', 'status', 'snaps', 'states', 'levels',
           'agree', 'lack', 'invent', 'theirs_bigger']
          + [b[2] for b in BANDS])


def load_book_raw(suf, match, leg):
    fs = sorted(glob.glob(os.path.join(CANON, 'books' + suf, match,
                                       f'*-{leg}__*.book.p*.parquet')))
    if not fs:
        return None
    cols = ['ts', 'bid', 'ask', 'bid_prices', 'bid_sizes', 'ask_prices', 'ask_sizes']
    return pd.concat([pd.read_parquet(f, columns=cols) for f in fs]).sort_values('ts')


def one_leg(match, leg, tier):
    suf = TREE.get(tier, '')
    bk = load_book_raw(suf, match, leg)
    if bk is None:
        return dict(match=match, leg=leg, tier=tier, status='no book')
    w = windows().loc[match]
    snaps = get_snaps(f'KXWCGAME-{match}-{leg}', int(w.t0_ms), int(w.t1_ms))
    if not snaps:
        return dict(match=match, leg=leg, tier=tier, status='no predexon data')

    ours = {int(r.ts): (dict(zip(r.bid_prices.tolist(), r.bid_sizes.tolist())),
                        dict(zip(r.ask_prices.tolist(), r.ask_sizes.tolist())),
                        r.bid, r.ask)
            for r in bk.itertuples()}
    lad, dist, same = [], [], []
    nstate = 0
    for s in snaps:
        ts = s.get('timestamp') or s.get('ts')
        if ts is None or int(ts) not in ours:
            continue                      # exact millisecond only
        nstate += 1
        ob, oa, bbid, bask = ours[int(ts)]
        for key, mine, isbid in (('yes_bids', ob, True), ('yes_asks', oa, False)):
            th = {int(float(x['price'])): float(x['size']) for x in (s.get(key) or [])}
            for p in set(th) | set(mine):
                t, o = th.get(p, 0.0), mine.get(p, 0.0)
                lad.append((t, o))
                dist.append((bbid - p) if isbid else (p - bask))
                same.append(abs(t - o) < DUST)
    if not lad:
        return dict(match=match, leg=leg, tier=tier, status='no exact-ms overlap',
                    snaps=len(snaps))
    th = np.array([x[0] for x in lad]); ou = np.array([x[1] for x in lad])
    dist = np.array(dist); same = np.array(same)
    bad = ~same
    r = dict(match=match, leg=leg, tier=tier, status='ok',
             snaps=len(snaps), states=nstate, levels=len(same),
             agree=float(same.mean()),
             lack=int(((ou == 0) & (th > DUST)).sum()),
             invent=int(((th == 0) & (ou > DUST)).sum()),
             theirs_bigger=float((th[bad] > ou[bad]).mean()) if bad.any() else np.nan)
    for lo, hi, name in BANDS:
        m = (dist >= lo) & (dist <= hi)
        r[name] = float(same[m].mean()) if m.any() else np.nan
    del lad, th, ou, dist, same, ours, bk, snaps; gc.collect()
    return r


def main():
    if not os.environ.get('PREDEXON_API_KEY'):
        sys.exit('PREDEXON_API_KEY is not set')
    q = pd.read_csv(os.path.join(CANON, 'leg_quality.csv'))
    tiers = os.environ.get('TIERS')
    if tiers:
        q = q[q.tier.isin(tiers.split(','))]
    # resume: skip legs already measured
    done = set()
    if os.path.exists(OUT):
        prev = pd.read_csv(OUT)
        done = set(zip(prev.match, prev.leg))
        print(f"resuming: {len(done)} legs already measured")
    todo = [r for r in q.itertuples() if (r.match, r.leg) not in done]
    lim = os.environ.get('LIMIT')
    if lim:
        todo = todo[:int(lim)]
    print(f"{len(todo)} legs to measure\n", flush=True)

    new = not os.path.exists(OUT)
    fh = open(OUT, 'a', newline='')
    wr = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction='ignore')
    if new:
        wr.writeheader()
    for i, r in enumerate(todo, 1):
        try:
            res = one_leg(r.match, r.leg, r.tier)
        except Exception as e:
            res = dict(match=r.match, leg=r.leg, tier=r.tier,
                       status=f'{type(e).__name__}: {str(e)[:60]}')
        wr.writerow(res); fh.flush()      # flush per leg so a kill loses nothing
        if res.get('status') == 'ok':
            print(f"[{i}/{len(todo)}] {r.match}-{r.leg:<4} {r.tier:<16}"
                  f" agree {res['agree']:7.2%}  touch {res['touch']:6.1%}"
                  f"  lack {res['lack']:>6,}  invent {res['invent']:>5,}", flush=True)
        else:
            print(f"[{i}/{len(todo)}] {r.match}-{r.leg:<4} {r.tier:<16} {res['status']}",
                  flush=True)
    fh.close()

    d = pd.read_csv(OUT)
    ok = d[d.status == 'ok']
    print(f"\n=== {len(ok)} legs measured, {len(d)-len(ok)} not ===")
    if not len(ok):
        return
    print(d[d.status != 'ok'].status.value_counts().to_string())
    print("\n=== agreement by tier ===")
    print(ok.groupby('tier').agg(
        legs=('leg', 'size'), median=('agree', 'median'),
        worst=('agree', 'min'), best=('agree', 'max'),
        touch=('touch', 'median'), deep=('d31up', 'median'),
        lack=('lack', 'median'), invent=('invent', 'median')).round(4).to_string())
    print("\n=== legs below 99% inside depth_clean (these need explaining) ===")
    bad = ok[(ok.tier == 'depth_clean') & (ok.agree < 0.99)]
    print(f"{len(bad)} of {int((ok.tier=='depth_clean').sum())}")
    if len(bad):
        print(bad[['match', 'leg', 'agree', 'touch', 'd31up', 'lack', 'invent']]
              .sort_values('agree').to_string(index=False))


if __name__ == '__main__':
    main()
