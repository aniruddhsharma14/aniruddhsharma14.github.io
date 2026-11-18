"""
Stage 0d -- CROSS-LEG CONSISTENCY, a per-state data-quality signal.

The three legs of a match are mutually exclusive and exhaustive, so at every instant
their prices must sum to 100. That makes the sum a free, model-free check on our own
reconstruction at EVERY state -- not just a once-per-match sanity test.

It earns its place: ~6% of late-match states show a three-leg mid sum far from 100
(one observed state had COL 20/21, POR 2/3, TIE 18/19 -- all 1-tick spreads, summing
to 41.5 instead of 100). Three legs cannot each be independently mispriced, so such a
state means at least one leg's touch is stale or drained. Those states are invisible
to per-leg checks: each leg looks perfectly healthy on its own.

Written per match so any experiment can require internal consistency before using a
state, rather than each experiment reinventing a filter.

Usage: python3 stage0_consistency.py [budget_s]          (live tree, resumable)
       CANON_SUFFIX=_v2 python3 stage0_consistency.py    (rebuilt tree)

Covers every match present in books<SUFFIX>/ and writes consistency<SUFFIX>/.
A rebuilt tree gets its own consistency files: flags computed from one set of books
must never be paired with another.
"""
import sys, os, glob
import pandas as pd, numpy as np
import pyarrow as pa, pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import load_book, legs, windows, CANON, SUF

TOL = 2.0   # cents; |mid_sum - 100| within this is consistent


def build(match):
    L = legs(match)
    d = {k: load_book(match, k, columns=['bid', 'ask', 'mid', 'spread']) for k in L}
    if any(len(v) == 0 for v in d.values()):
        return None
    allts = sorted(set().union(*[set(v.ts) for v in d.values()]))
    A = pd.DataFrame(index=allts)
    for k, v in d.items():
        vv = v[~v.ts.duplicated(keep='last')].set_index('ts')
        for c in ('bid', 'ask', 'mid', 'spread'):
            A[f'{k}_{c}'] = vv[c].reindex(allts).ffill()
        last = pd.Series(vv.index, index=vv.index).reindex(allts).ffill()
        A[f'{k}_age'] = np.asarray(allts) - last.values
    A = A.dropna()
    if not len(A):
        return None
    out = pd.DataFrame(dict(
        ts=A.index.astype('int64'),
        mid_sum=A[[f'{k}_mid' for k in L]].sum(axis=1).values,
        ask_sum=A[[f'{k}_ask' for k in L]].sum(axis=1).values,
        bid_sum=A[[f'{k}_bid' for k in L]].sum(axis=1).values,
        max_spread=A[[f'{k}_spread' for k in L]].max(axis=1).values,
        max_age_ms=A[[f'{k}_age' for k in L]].max(axis=1).values))
    out['consistent'] = (out.mid_sum - 100).abs() <= TOL
    return out


def main():
    od = os.path.join(CANON, 'consistency' + SUF); os.makedirs(od, exist_ok=True)
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 1e9
    import time; t0 = time.time()
    bd = os.path.join(CANON, 'books' + SUF)
    ms = sorted(d for d in os.listdir(bd) if os.path.isdir(os.path.join(bd, d)))
    for m in ms:
        f = os.path.join(od, f'{m}.parquet')
        if not os.path.exists(f):
            c = build(m)
            if c is None:
                continue
            pq.write_table(pa.Table.from_pandas(c, preserve_index=False), f, compression='zstd')
            print(f'  {m}: {len(c):,} states, {c.consistent.mean():.2%} consistent', flush=True)
            if time.time() - t0 > budget:
                print('BUDGET REACHED'); return
    print('CONSISTENCY COMPLETE')


if __name__ == '__main__':
    main()
