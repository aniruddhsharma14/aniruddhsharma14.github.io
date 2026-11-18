"""
Compare our reconstructed LADDER against Predexon's, state by state.

This replaces the P1 gate in predexon_deltas.py, which was wrong. P1 compared
their NET CHANGE across an interval against the sum of our deltas inside it.
Their snapshots are ~409 ms apart and our deltas arrive every ~74 ms, so each
interval holds ~5 events and a few ms of timestamp skew pushes one of them over
a boundary, failing the whole cell. It therefore reads ~3% whatever the book
quality: it measured boundary alignment, not correctness.

This script compares full ladders at EXACTLY equal milliseconds. No intervals,
no boundaries, nothing to misassign. It is the metric that gave the real 48.8%
on 26JUN24RSAKOR, against P1's 3.1% on the same data.

    export PREDEXON_API_KEY=...
    MATCH=26JUN14GERCUW python3 pipeline/compare_states.py                 # live
    CANON_SUFFIX=_v2 MATCH=26JUN14GERCUW python3 pipeline/compare_states.py  # rebuilt

Snapshots come from the same canonical/predexon_raw cache, so the two runs see
identical external data and the ONLY difference is which book is compared.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import load_book, legs, windows, CANON, SUF
from predexon_deltas import get_snaps

MATCH = os.environ.get('MATCH', '26JUN14GERCUW')
DUST = 0.5


def compare(match, leg, snaps, bk):
    """One row per (snapshot, ladder, price) where either side has size."""
    ours = {int(r.ts): (dict(zip(r.bid_prices.tolist(), r.bid_sizes.tolist())),
                        dict(zip(r.ask_prices.tolist(), r.ask_sizes.tolist())),
                        r.bid, r.ask)
            for r in bk.itertuples()}
    rows = []
    for s in snaps:
        ts = s.get('timestamp') or s.get('ts')
        if ts is None or int(ts) not in ours:
            continue                      # exact millisecond only
        ob, oa, bbid, bask = ours[int(ts)]
        for lad, key, mine in (('bid', 'yes_bids', ob), ('ask', 'yes_asks', oa)):
            theirs = {int(float(x['price'])): float(x['size']) for x in (s.get(key) or [])}
            for p in set(theirs) | set(mine):
                t, o = theirs.get(p, 0.0), mine.get(p, 0.0)
                dist = (bbid - p) if lad == 'bid' else (p - bask)
                rows.append((lad, p, dist, t, o))
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=['ladder', 'price', 'dist', 'theirs', 'ours'])
    d['same'] = (d.theirs - d.ours).abs() < DUST
    return d


def main():
    w = windows().loc[MATCH]
    t0, t1 = int(w.t0_ms), int(w.t1_ms)
    print(f"book tree: canonical/books{SUF or ' (live)'}\n")
    out = []
    for leg in legs(MATCH):
        snaps = get_snaps(f'KXWCGAME-{MATCH}-{leg}', t0, t1)
        if not snaps:
            print(f"[{leg}] no data"); continue
        try:
            bk = load_book(MATCH, leg)
        except Exception as e:
            print(f"[{leg}] {type(e).__name__}: {e}"); continue
        d = compare(MATCH, leg, snaps, bk)
        if d is None:
            print(f"[{leg}] no exact-millisecond overlap"); continue
        sub = int(((d.ours == 0) & (d.theirs > DUST)).sum())
        sup = int(((d.theirs == 0) & (d.ours > DUST)).sum())
        bad = d[~d.same]
        out.append(dict(leg=leg, levels=len(d), agree=float(d.same.mean()),
                        ours_missing=sub, ours_extra=sup,
                        theirs_bigger=float((bad.theirs > bad.ours).mean()) if len(bad) else np.nan))
        print(f"[{leg}] {len(d):>7,} level comparisons   AGREE {d.same.mean():.2%}"
              f"   levels we lack {sub:,}   levels we invent {sup:,}", flush=True)
        for lo, hi, lab in ((0, 0, 'touch'), (1, 3, '1-3'), (4, 10, '4-10'),
                            (11, 30, '11-30'), (31, 200, '31+')):
            m = d[(d.dist >= lo) & (d.dist <= hi)]
            if len(m):
                print(f"        {lab:>6}: {m.same.mean():6.1%}  ({len(m):,} levels)")

    if not out:
        sys.exit('nothing compared')
    r = pd.DataFrame(out)
    r.to_csv(os.path.join(CANON, f'state_cmp{SUF}_{MATCH}.csv'), index=False)
    print(f"\n=== {MATCH}   books{SUF or ' (live)'} ===")
    print(f"  ladder levels agreeing : {r.agree.median():.2%} median leg,"
          f" {r.agree.min():.2%} worst")
    print(f"  levels we LACK  (they have size, we have none): {int(r.ours_missing.sum()):,}")
    print(f"  levels we INVENT (we have size, they have none): {int(r.ours_extra.sum()):,}")
    print("\n  Reference: live tree on 26JUN24RSAKOR-KOR measured 48.78%,")
    print("  with our book a strict SUBSET of theirs (508 lacked, 0 invented).")
    print("  A working repair should push agreement well above that and keep")
    print("  'invented' at zero -- seeding can only ADD size, never create levels")
    print("  that do not exist. A non-zero 'invented' count means the seed was")
    print("  stale, which is the failure mode C-class legs were excluded for.")


if __name__ == '__main__':
    main()
