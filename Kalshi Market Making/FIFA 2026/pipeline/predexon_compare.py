"""
Compare Predexon's reconstruction against ours, across many matches.

Answers two questions that gate everything downstream:

 Q1  DO WE AGREE?  If their best bid/ask disagrees with ours on matches we both
     hold, then testing a model on their data where we have none is void: a
     failure would not distinguish a bad model from different data.

 Q2  IS THEIR RATE OURS?  Their docs say snapshots are emitted on every book
     change. If true, differencing consecutive snapshots recovers the deltas
     exactly and every event-level feature survives. If they are sampled
     instead, differencing NETS events together -- which would reintroduce
     precisely the ambiguity we proved is absent in the raw feed, and would kill
     fill/cancel/add classification, queue dynamics and any sequence-keyed
     feature on their data.

     The decisive number is their update count over ours in the same window.
     ~1.0 means per-change. Much below 1.0 means sampled.

SAMPLING: a 10-minute mid-match window per match, not the whole match. At 1
request/sec a full match takes minutes; a window takes seconds and still yields
thousands of states, which is far more than needed to measure either question.

    export PREDEXON_API_KEY=...
    python3 predexon_compare.py            # explore matches
    MATCHES=26JUN13BRAMAR,26JUN11KORCZE python3 predexon_compare.py
"""
from __future__ import annotations
import os, sys, time, json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import load_book, legs, windows, split_matches, CANON

BASE = os.environ.get('PREDEXON_BASE', 'https://api.predexon.com')
PATH = os.environ.get('PREDEXON_PATH', '/v2/kalshi/orderbooks-subcent')
KEY = os.environ.get('PREDEXON_API_KEY', '')

WINDOW_MIN = int(os.environ.get('WINDOW_MIN', 10))   # minutes sampled per match
OFFSET_MIN = int(os.environ.get('OFFSET_MIN', 30))   # minutes after kickoff
SLEEP_S = 1.1                                        # free tier: 1 req/sec
MAX_REQ = 40                                         # per leg, safety stop


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%H:%M:%S')


def _get(params):
    r = requests.get(BASE + PATH, params=params,
                     headers={'x-api-key': KEY}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _rows(d):
    if isinstance(d, list):
        return d
    for k in ('snapshots', 'data', 'orderbooks', 'results'):
        if isinstance(d.get(k), list):
            return d[k]
    return []


def _cursor(d):
    """Follow has_more, not the presence of a key. Their docs return the key on the
    last page too, so keying off the key alone re-requests the tail to MAX_REQ."""
    pg = d.get('pagination') or {}
    if pg and not pg.get('has_more', True):
        return None
    return pg.get('pagination_key') or d.get('pagination_key')


def _touch(s):
    """Best bid and best ask from the ladders.

    Derived, not read from the summary fields, and float()-ed first: subcent
    returns DECIMAL STRINGS, so max() on raw strings compares lexically and
    "9.00" > "58.00". That would silently pick the wrong level on every row."""
    bids = s.get('yes_bids') or []
    asks = s.get('yes_asks') or []
    f = lambda x: float(x)
    bb = max((f(b['price']) for b in bids), default=None)
    ba = min((f(a['price']) for a in asks), default=None)
    return bb, ba, len(bids), len(asks)


def fetch(ticker, t0, t1):
    out, cur, n = [], None, 0
    while n < MAX_REQ:
        p = dict(ticker=ticker, limit=2000, start_time=t0, end_time=t1)
        if cur:
            p['pagination_key'] = cur
        d = _get(p)
        rows = _rows(d)
        out += rows
        n += 1
        cur = _cursor(d)
        if not cur or not rows:
            break
        time.sleep(SLEEP_S)
    return out


def compare_leg(match, leg, t0, t1):
    tk = f'KXWCGAME-{match}-{leg}'
    snaps = fetch(tk, t0, t1)
    if not snaps:
        return dict(match=match, leg=leg, status='no data')

    recs = []
    for s in snaps:
        bb, ba, nb, na = _touch(s)
        recs.append((s.get('timestamp') or s.get('ts'), bb, ba, nb, na))
    ext = (pd.DataFrame(recs, columns=['ts', 'bid', 'ask', 'nb', 'na'])
           .dropna(subset=['ts']).sort_values('ts').reset_index(drop=True))

    ours = load_book(match, leg, columns=['bid', 'ask'])
    ours = ours[(ours.ts >= t0) & (ours.ts <= t1)]
    if not len(ours) or not len(ext):
        return dict(match=match, leg=leg, status='empty window')

    m = pd.merge_asof(
        ours[['ts', 'bid', 'ask']].rename(columns={'bid': 'ob', 'ask': 'oa'}),
        ext[['ts', 'bid', 'ask']].rename(columns={'bid': 'eb', 'ask': 'ea'}),
        on='ts', direction='backward', tolerance=1000).dropna()

    secs = (t1 - t0) / 1000
    return dict(
        match=match, leg=leg, status='ok',
        ours_n=len(ours), theirs_n=len(ext),
        rate_ratio=len(ext) / max(len(ours), 1),
        ours_per_s=len(ours) / secs, theirs_per_s=len(ext) / secs,
        theirs_med_gap_ms=float(ext.ts.diff().median()),
        ours_med_gap_ms=float(ours.ts.diff().median()),
        theirs_levels=float(ext.nb.median()),
        theirs_crossed=float((ext.bid >= ext.ask).mean()),
        matched=len(m),
        bid_same=float((m.ob == m.eb).mean()) if len(m) else np.nan,
        ask_same=float((m.oa == m.ea).mean()) if len(m) else np.nan,
    )


def main():
    if not KEY:
        sys.exit('PREDEXON_API_KEY is not set')
    env = os.environ.get('MATCHES')
    ms = env.split(',') if env else split_matches('explore')
    w = windows()

    rows = []
    for i, mt in enumerate(ms, 1):
        k = int(w.loc[mt, 'kickoff_ms'])
        t0 = k + OFFSET_MIN * 60_000
        t1 = t0 + WINDOW_MIN * 60_000
        if t1 > int(w.loc[mt, 'end_ms']):
            t1 = int(w.loc[mt, 'end_ms']); t0 = max(k, t1 - WINDOW_MIN * 60_000)
        print(f"[{i}/{len(ms)}] {mt}  {iso(t0)}-{iso(t1)} UTC", flush=True)
        for lg in legs(mt):
            try:
                r = compare_leg(mt, lg, t0, t1)
            except Exception as e:
                r = dict(match=mt, leg=lg, status=f'{type(e).__name__}: {str(e)[:60]}')
            rows.append(r)
            s = r.get('status')
            if s == 'ok':
                print(f"    {lg}: ours {r['ours_n']:>6,} vs theirs {r['theirs_n']:>6,}"
                      f"  ratio {r['rate_ratio']:.2f}"
                      f"  bid {r['bid_same']:.1%} ask {r['ask_same']:.1%}"
                      f"  crossed {r['theirs_crossed']:.2%}", flush=True)
            else:
                print(f"    {lg}: {s}", flush=True)
            time.sleep(SLEEP_S)

    df = pd.DataFrame(rows)
    out = os.path.join(CANON, 'predexon_compare.csv')
    df.to_csv(out, index=False)
    ok = df[df.status == 'ok']
    if not len(ok):
        sys.exit('\nno legs compared successfully')

    print(f"\nwrote {out}\n")
    print("=== Q1  DO WE AGREE? ===")
    print(f"  best bid identical : {ok.bid_same.median():.1%} median, {ok.bid_same.min():.1%} worst")
    print(f"  best ask identical : {ok.ask_same.median():.1%} median, {ok.ask_same.min():.1%} worst")
    print(f"  their crossed states: {ok.theirs_crossed.max():.3%} worst   (ours: 0.000%)")

    print("\n=== Q2  IS THEIR RATE OURS? ===")
    print(f"  updates/sec  ours {ok.ours_per_s.median():.1f}   theirs {ok.theirs_per_s.median():.1f}")
    print(f"  ratio theirs/ours : {ok.rate_ratio.median():.2f} median "
          f"[{ok.rate_ratio.min():.2f}, {ok.rate_ratio.max():.2f}]")
    print(f"  median gap    ours {ok.ours_med_gap_ms.median():.0f} ms   "
          f"theirs {ok.theirs_med_gap_ms.median():.0f} ms")
    print(f"  their depth levels : {ok.theirs_levels.median():.0f}")

    print("\n=== VERDICT ===")
    r = ok.rate_ratio.median(); b = ok.bid_same.median(); x = ok.theirs_crossed.max()
    if x > 0.001:
        print("  THEIR BOOK CROSSES. Their YES/NO inversion is wrong. Do not use.")
    elif b < 0.80:
        print("  DISAGREES with our reconstruction. Cross-source test is void until explained.")
    elif r >= 0.8:
        print("  Agrees, and their rate matches ours: snapshots look per-change.")
        print("  -> differencing recovers deltas; event-level features survive.")
    elif r >= 0.3:
        print("  Agrees, but their rate is LOWER than ours: snapshots are sampled.")
        print("  -> state features transfer; fill/cancel/add and queue dynamics do NOT.")
    else:
        print("  Agrees on price, but far coarser. Price paths only.")


if __name__ == '__main__':
    main()
