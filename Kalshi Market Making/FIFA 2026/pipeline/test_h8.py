"""
H8 -- is queue depletion at the touch dominated by cancellation, not trading?

Predictions and kill criteria were committed to hypotheses.md BEFORE this ran.
This script only reports; it does not get to decide what counts as success.

Reports PER MATCH, never pooled. Pooling would let one busy match dominate and
hide between-match variation, and between-match variation is the whole question.

The unit of independence is the tournament GROUP. The three legs of a match are
mechanically linked by the sum-to-100 constraint, and matches within a group
share teams. 24 exploration matches sit in 8 groups, so n = 8.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow_decomp import decompose
from canon_load import load_book, split_matches, legs, splits


def touch_flow(match, leg):
    """Volume added, cancelled and traded AT THE TOUCH for one leg."""
    ev, rep = decompose(match, leg)
    bk = load_book(match, leg, columns=['bid', 'ask'])
    if not len(ev) or not len(bk):
        return None
    b = bk[['ts', 'bid', 'ask']].sort_values('ts')
    e = pd.merge_asof(ev.sort_values('ts'), b, on='ts', direction='backward')
    e = e.dropna(subset=['bid', 'ask'])
    # distance from the SAME-side best. Absolute price would confound the level
    # with the price regime: a leg at 8 and a leg at 50 are different worlds.
    e['dist'] = np.where(e.ladder == 'bid', e.bid - e.yes_price, e.yes_price - e.ask)
    t = e[e.dist == 0]
    if not len(t):
        return None
    vol = t.groupby('kind').amount.apply(lambda x: float(np.abs(x).sum()))
    cancel = float(vol.get('cancel', 0.0)); fill = float(vol.get('fill', 0.0))
    out = cancel + fill
    if out <= 0:
        return None
    mag = t.assign(mag=t.amount.abs())
    med = mag.groupby('kind')['mag'].median()
    p90 = mag.groupby('kind')['mag'].quantile(0.90)
    return dict(match=match, leg=leg,
                cancel_share=cancel / out,
                cancel_vol=cancel, fill_vol=fill,
                med_fill=float(med.get('fill', np.nan)),
                med_cancel=float(med.get('cancel', np.nan)),
                p90_fill=float(p90.get('fill', np.nan)),
                p90_cancel=float(p90.get('cancel', np.nan)),
                n_touch_events=len(t))


def main():
    ms = split_matches('explore')
    off = int(os.environ.get('OFF', 0)); lim = int(os.environ.get('LIM', len(ms)))
    ms = ms[off:off + lim]
    rows = []
    for m in ms:
        for lg in legs(m):
            try:
                r = touch_flow(m, lg)
            except Exception as e:
                print(f"  {m} {lg}: {type(e).__name__} {e}"); continue
            if r:
                rows.append(r)
        print(f"  done {m}", flush=True)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'canonical', f'h8_legs_{off}.csv')
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}  ({len(rows)} legs)")


if __name__ == '__main__':
    main()
