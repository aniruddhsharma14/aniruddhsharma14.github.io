"""
H10 -- can Predexon's snapshots be differenced back into our deltas?

Registered in hypotheses.md BEFORE this ran. This script reports; it does not
decide what counts as success.

    export PREDEXON_API_KEY=...
    python3 pipeline/predexon_deltas.py                 # 26JUN24RSAKOR
    MATCH=26JUN24SCOBRA python3 pipeline/predexon_deltas.py

Snapshots are cached to canonical/predexon_raw/<ticker>.json.gz on first fetch.
Re-runs read the cache and spend no requests, so the analysis can be reworked
without re-paying for the data. Delete the file to refetch.
"""
from __future__ import annotations
import os, sys, time, gzip, json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import windows, legs, CANON
from flow_decomp import decompose
import predexon_compare as px

MATCH = os.environ.get('MATCH', '26JUN24RSAKOR')
CACHE = os.path.join(CANON, 'predexon_raw')


# ---------------------------------------------------------------- fetch & cache

def get_snaps(ticker, t0, t1):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, f'{ticker}.json.gz')
    if os.path.exists(fp):
        with gzip.open(fp, 'rt') as f:
            d = json.load(f)
        print(f"    cache: {len(d):,} snapshots", flush=True)
        return d
    px.MAX_REQ = 200          # a full match window is ~12 pages at limit 2000
    t = time.time()
    d = px.fetch(ticker, t0, t1)
    print(f"    fetched {len(d):,} snapshots in {time.time()-t:.0f}s", flush=True)
    with gzip.open(fp, 'wt') as f:
        json.dump(d, f)
    return d


def to_levels(snaps):
    """Flatten to one row per (snapshot, ladder, price, size).

    float() on price, never the raw string: subcent returns decimal strings and
    lexical ordering puts "9.00" above "58.00"."""
    rows = []
    for i, s in enumerate(snaps):
        ts = s.get('timestamp') or s.get('ts')
        if ts is None:
            continue
        for lad, key in (('bid', 'yes_bids'), ('ask', 'yes_asks')):
            for lv in (s.get(key) or []):
                rows.append((i, int(ts), lad, float(lv['price']), float(lv['size'])))
    d = pd.DataFrame(rows, columns=['snap', 'ts', 'ladder', 'price', 'size'])
    # their pages can overlap; one row per (snapshot, level) and a clean clock
    d = d.drop_duplicates(['ts', 'ladder', 'price'], keep='last')
    order = {t: i for i, t in enumerate(sorted(d.ts.unique()))}
    d['snap'] = d.ts.map(order)
    return d.sort_values(['snap', 'ladder', 'price']).reset_index(drop=True)


# ---------------------------------------------------------------- the two sides

def their_net(lv):
    """Net size change per (interval, ladder, price), from differencing.

    A level absent from a snapshot is size 0, not missing -- an order that is
    fully cancelled leaves the ladder, and dropping the row would hide exactly
    the event we are looking for. The pivot makes that explicit."""
    w = (lv.pivot_table(index=['ladder', 'price'], columns='snap',
                        values='size', fill_value=0.0)
           .sort_index(axis=1))
    diff = w.diff(axis=1).iloc[:, 1:]                 # interval k = (t_k, t_k+1]
    s = diff.stack()
    s = s[s != 0.0].rename('their_net').reset_index()
    return s.rename(columns={'snap': 'interval'})


def our_events(match, leg, ts_edges):
    """Our raw deltas, bucketed into their intervals.

    Half-open (t_k, t_k+1] to match the differencing: a delta landing exactly on
    t_k+1 is already visible in that snapshot, so it belongs to interval k."""
    ev, _ = decompose(match, leg)
    ev = ev[(ev.ts > ts_edges[0]) & (ev.ts <= ts_edges[-1])].copy()
    ev['interval'] = np.searchsorted(ts_edges, ev.ts.values, side='left') - 1
    ev = ev[ev.interval >= 0]
    ev['price'] = ev.yes_price.astype(float)
    return ev


# ---------------------------------------------------------------- measurement

def analyse(match, leg, lv, touch_px):
    ts_edges = np.array(sorted(lv.ts.unique()))
    if len(ts_edges) < 3:
        return None
    theirs = their_net(lv)
    ours = our_events(match, leg, ts_edges)
    if not len(ours):
        return None

    cell = ['interval', 'ladder', 'price']
    agg = ours.groupby(cell).agg(our_net=('amount', 'sum'),
                                 n=('amount', 'size'),
                                 n_pos=('amount', lambda x: int((x > 0).sum())),
                                 n_neg=('amount', lambda x: int((x < 0).sum())),
                                 ).reset_index()

    # P1 state fidelity: does their net change equal the sum of our deltas?
    j = agg.merge(theirs, on=cell, how='outer').fillna(
        {'our_net': 0.0, 'their_net': 0.0, 'n': 0, 'n_pos': 0, 'n_neg': 0})
    # 0.5 is the DUST threshold used throughout the reconstruction
    j['match'] = (j.our_net - j.their_net).abs() < 0.5
    fidelity = float(j.match.mean())

    # P2 event recovery: our deltas that sit ALONE in their cell
    ours = ours.merge(agg[cell + ['n', 'n_pos', 'n_neg']], on=cell, how='left')
    recovery = float((ours.n == 1).mean())

    # P3 at the touch vs away. 'touch' is defined per event against the
    # same-side best at that moment, so a thin leg and a 50c leg are comparable.
    ours = ours.merge(touch_px, on='ts', how='left')
    ours['dist'] = np.where(ours.ladder == 'bid',
                            ours.bid - ours.price, ours.price - ours.ask)
    at, away = ours[ours.dist == 0], ours[ours.dist > 0]
    rec_touch = float((at.n == 1).mean()) if len(at) else np.nan
    rec_away = float((away.n == 1).mean()) if len(away) else np.nan

    # P4 classification loss: filled volume in a netted cell, and in the
    # irreducible case where the cell also holds an opposite-signed event
    f = ours[ours.kind == 'fill']
    fv = float(f.amount.abs().sum())
    fill_netted = float(f.loc[f.n >= 2, 'amount'].abs().sum() / fv) if fv else np.nan
    mixed = (f.n_pos > 0) & (f.n_neg > 0)
    fill_mixed = float(f.loc[mixed, 'amount'].abs().sum() / fv) if fv else np.nan

    allv = float(ours.amount.abs().sum())
    mx = (ours.n_pos > 0) & (ours.n_neg > 0)
    return dict(
        match=match, leg=leg,
        snaps=len(ts_edges), our_events=len(ours), cells=len(j),
        fidelity=fidelity,
        fid_cells_bad=int((~j.match).sum()),
        recovery=recovery,
        rec_touch=rec_touch, rec_away=rec_away,
        rec_gap=(rec_away - rec_touch) if np.isfinite(rec_touch) else np.nan,
        med_per_cell=float(ours.n.median()),
        p90_per_cell=float(ours.n.quantile(0.90)),
        mixed_vol_share=float(ours.loc[mx, 'amount'].abs().sum() / allv) if allv else np.nan,
        fill_netted_share=fill_netted,
        fill_mixed_share=fill_mixed,
    )


def main():
    if not px.KEY:
        sys.exit('PREDEXON_API_KEY is not set')
    w = windows().loc[MATCH]
    t0, t1 = int(w.t0_ms), int(w.t1_ms)
    from canon_load import load_book

    rows = []
    for leg in legs(MATCH):
        print(f"[{MATCH}-{leg}]", flush=True)
        snaps = get_snaps(f'KXWCGAME-{MATCH}-{leg}', t0, t1)
        if not snaps:
            print("    no data"); continue
        lv = to_levels(snaps)
        bk = load_book(MATCH, leg, columns=['bid', 'ask'])[['ts', 'bid', 'ask']]
        r = analyse(MATCH, leg, lv, bk.drop_duplicates('ts'))
        if not r:
            print("    not enough overlap"); continue
        rows.append(r)
        print(f"    fidelity {r['fidelity']:.3%}  recovery {r['recovery']:.1%}"
              f"  (touch {r['rec_touch']:.1%} / away {r['rec_away']:.1%})"
              f"  fill netted {r['fill_netted_share']:.1%}", flush=True)

    if not rows:
        sys.exit('nothing measured')
    df = pd.DataFrame(rows)
    out = os.path.join(CANON, f'h10_{MATCH}.csv')
    df.to_csv(out, index=False)
    print(f"\nwrote {out}\n")

    med = df.median(numeric_only=True)
    print("=== P1  STATE FIDELITY  (predicted >= 99%) ===")
    print(f"  cells where their net == our summed delta : {med.fidelity:.3%}")
    print(f"  mismatched cells, worst leg              : {int(df.fid_cells_bad.max()):,}")
    if med.fidelity < 0.99:
        print("\n  P1 FAILS. The test is VOID, not supportive. Either our interval")
        print("  arithmetic or price mapping is wrong, or their states are not")
        print("  faithful. Do not read anything below until this is explained.")
        return

    print("\n=== P2  EVENT RECOVERY  (predicted < 50%) ===")
    print(f"  our deltas alone in their cell : {med.recovery:.1%}")
    print(f"  deltas per cell, median        : {med.med_per_cell:.0f}   p90 {med.p90_per_cell:.0f}")

    print("\n=== P3  WORSE AT THE TOUCH  (predicted gap >= 10pp) ===")
    print(f"  recovery at touch {med.rec_touch:.1%}   away {med.rec_away:.1%}"
          f"   gap {med.rec_gap*100:.1f}pp")

    print("\n=== P4  CLASSIFICATION LOSS  (predicted >= 20%) ===")
    print(f"  filled volume in a netted cell        : {med.fill_netted_share:.1%}")
    print(f"  filled volume in a MIXED-SIGN cell    : {med.fill_mixed_share:.1%}")
    print(f"  all volume in a mixed-sign cell       : {med.mixed_vol_share:.1%}")

    print("\n=== VERDICT ===")
    if med.recovery > 0.90 and med.mixed_vol_share < 0.05:
        print("  H10 REJECTED. Differencing recovers their events. Their free")
        print("  snapshots substitute for the tick feed; the Q2 verdict was wrong.")
    else:
        print("  H10 SUPPORTED. Differencing does not recover the events.")
        print(f"  {1-med.recovery:.0%} of deltas are netted away, and {med.fill_mixed_share:.0%} of filled")
        print("  volume sits in cells where sign is ambiguous. Their data carries")
        print("  state features only. No flow decomposition, no queue dynamics.")


if __name__ == '__main__':
    main()
