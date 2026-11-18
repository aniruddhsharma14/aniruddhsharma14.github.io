"""
Measure the missing resting size in every leg, from our own data alone.

THE ARGUMENT. A price level cannot hold negative size. So if the running sum of
our deltas at one level ever goes below zero, the shortfall is exactly the size
that was resting there before our first message and that we never saw arrive.
No external source is needed: it is arithmetic on our own tape.

    deficit(level) = max(0, -min(cumulative sum of deltas at that level))

WHERE THIS IS VALID. Only for legs the reconstruction built WITHOUT a snapshot.
If a populated snapshot was applied, it supplied the true starting sizes, so a
negative cumsum of deltas alone is expected and means nothing. Those legs are
reported separately and get NO deficit number -- claiming one would be the same
class of error as the 250 ms matcher: a real measurement of the wrong thing.

The manifest's n_snapshots_applied decides which case a leg is in.

    python3 pipeline/validate_sizes.py              # all tier-A/B matches
    OFF=0 LIM=8 python3 pipeline/validate_sizes.py  # chunked, 180s call limit

Writes canonical/size_deficit_{OFF}.csv (per leg) and
       canonical/size_deficit_levels_{OFF}.csv (per leg and price).
"""
from __future__ import annotations
import os, sys, glob
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import matches, legs, load_raw, load_book, CANON

DUST = 0.5


def manifest():
    fs = glob.glob(os.path.join(CANON, '_manifest', '*.csv'))
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    # a leg can appear on several tape-days; it is seeded if ANY day applied one
    return d.groupby('ticker').agg(snaps_applied=('n_snapshots_applied', 'sum'),
                                   neg_clamps=('neg_clamps', 'sum'),
                                   seq_gaps=('seq_gaps', 'sum'))


def deficits(match, leg):
    """Per-level deficit for one leg, plus which levels ever held the touch."""
    r = load_raw(match, leg)
    if not len(r):
        return None, None
    px = (r.price_dollars.astype(float) * 100).round().astype(int)
    amt = r.amount_fp.astype(float)
    # to YES terms: a 'no' delta at q is the YES ask ladder at 100-q
    is_yes = (r.side == 'yes').values
    out = pd.DataFrame({
        'ladder': np.where(is_yes, 'bid', 'ask'),
        'price': np.where(is_yes, px.values, 100 - px.values),
        'amount': amt.values,
        'ts': r.ts.values,
        'sequence': r.sequence.values,
    }).sort_values(['sequence', 'ts'], kind='stable')

    # min of the running sum, per level
    out['cum'] = out.groupby(['ladder', 'price']).amount.cumsum()
    g = out.groupby(['ladder', 'price']).agg(
        min_cum=('cum', 'min'), end_cum=('cum', 'last'),
        events=('amount', 'size'),
        removed=('amount', lambda x: float(-x[x < 0].sum())))
    g['deficit'] = (-g.min_cum).clip(lower=0.0)
    g = g.reset_index()
    g.insert(0, 'leg', leg); g.insert(0, 'match', match)

    # which price levels ever WERE the best bid or best ask? a deficit buried 40
    # ticks out is cosmetic; a deficit on a level that held the touch is not.
    try:
        bk = load_book(match, leg, columns=['bid', 'ask'])
        touch = set(bk.bid.dropna().astype(int)) | set(bk.ask.dropna().astype(int))
    except Exception:
        touch = set()
    g['was_touch'] = g.price.isin(touch)
    return g, touch


def main():
    import warnings
    warnings.filterwarnings('ignore')          # the size warning is the point here
    man = manifest()
    ms = matches('AB')
    off = int(os.environ.get('OFF', 0)); lim = int(os.environ.get('LIM', len(ms)))
    ms = ms[off:off + lim]

    lv, rows = [], []
    for m in ms:
        for leg in legs(m):
            tk = f'KXWCGAME-{m}-{leg}'
            mi = man.loc[tk] if tk in man.index else None
            seeded = bool(mi is not None and mi.snaps_applied > 0)
            try:
                g, touch = deficits(m, leg)
            except Exception as e:
                print(f"  {m} {leg}: {type(e).__name__} {e}", flush=True); continue
            if g is None:
                continue
            lv.append(g)
            bad = g[g.deficit >= DUST]
            bt = bad[bad.was_touch]
            rows.append(dict(
                match=m, leg=leg, seeded=seeded,
                snaps_applied=int(mi.snaps_applied) if mi is not None else -1,
                neg_clamps=int(mi.neg_clamps) if mi is not None else -1,
                levels=len(g), levels_with_deficit=len(bad),
                total_deficit=float(bad.deficit.sum()),
                max_deficit=float(bad.deficit.max()) if len(bad) else 0.0,
                touch_levels_with_deficit=len(bt),
                touch_deficit=float(bt.deficit.sum()) if len(bt) else 0.0,
                max_touch_deficit=float(bt.deficit.max()) if len(bt) else 0.0,
            ))
        print(f"  done {m}", flush=True)

    if not rows:
        sys.exit('nothing measured')
    d = pd.DataFrame(rows)
    pd.concat(lv, ignore_index=True).to_csv(
        os.path.join(CANON, f'size_deficit_levels_{off}.csv'), index=False)
    d.to_csv(os.path.join(CANON, f'size_deficit_{off}.csv'), index=False)
    print(f"\nwrote size_deficit_{off}.csv  ({len(d)} legs)")

    for label, sub in (('UNSEEDED  (deficit is a real measurement)', d[~d.seeded]),
                       ('SEEDED    (snapshot applied; deficit NOT meaningful)', d[d.seeded])):
        print(f"\n=== {label}  n={len(sub)} legs ===")
        if not len(sub):
            continue
        print(f"  legs with any deficit        : {int((sub.levels_with_deficit>0).sum())}/{len(sub)}")
        print(f"  levels affected, median leg  : {sub.levels_with_deficit.median():.0f}"
              f" of {sub.levels.median():.0f}")
        print(f"  total deficit, median leg    : {sub.total_deficit.median():,.0f} contracts")
        print(f"  TOUCH levels affected, median: {sub.touch_levels_with_deficit.median():.0f}")
        print(f"  deficit ON touch levels, med : {sub.touch_deficit.median():,.0f} contracts")


if __name__ == '__main__':
    main()
