"""
Predexon probe -- does their Kalshi orderbook archive cover our World Cup gaps,
and at what resolution?

CONFIRMED 2026-10-09:
  * endpoint  GET https://api.predexon.com/v2/kalshi/orderbooks
  * params    ticker, start_time, end_time (all required), limit, pagination_key
  * response  {"snapshots": [...], "pagination": {...}}
  * KXWCGAME IS covered. A probe for KXWCGAME-26JUN13BRAMAR-BRA returned full
    ladders, ~20+ price levels per side -- deeper than the 5 levels we store.
  * free tier reaches it.

Run this BEFORE paying anyone for data. It answers:
  1. Is the series in their archive?            -> yes, confirmed
  2. How deep are the snapshots?                -> probe prints level counts
  3. Does their book agree with ours?           -> compare

Question 3 is the one that matters. We hold 40 validated matches: settlement
correct 47/47, zero crossed books, ~96% internally consistent. That makes our
data the reference. If an outside source disagrees, one of the two is wrong, and
we must find out which before trusting either.

USAGE
    export PREDEXON_API_KEY=...          # never hardcode it
    python3 predexon_probe.py probe
    python3 predexon_probe.py compare

RATE LIMIT
Free plan is 1 request/second. Orderbook history does not count toward the
monthly quota, but the per-second limit still applies.
"""
from __future__ import annotations
import os, sys, time, json
from datetime import datetime, timezone

import requests

BASE_URL = os.environ.get("PREDEXON_BASE", "https://api.predexon.com")
PATH     = os.environ.get("PREDEXON_PATH", "/v2/kalshi/orderbooks-subcent")
KEY      = os.environ.get("PREDEXON_API_KEY", "")

# A match we already hold and validated. BRA-MAR, 13 June 2026, tier A, settled TIE.
REF_MATCH  = "26JUN13BRAMAR"
REF_LEG    = "BRA"
REF_TICKER = f"KXWCGAME-{REF_MATCH}-{REF_LEG}"
REF_T0_MS  = 1781388000000      # 2026-06-13 22:00:00 UTC  (kickoff)
REF_T1_MS  = 1781395680000      # 2026-06-14 00:08:00 UTC  (final whistle)

SLEEP_S = 1.1


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def _get(params):
    if not KEY:
        sys.exit("PREDEXON_API_KEY is not set. export it first.")
    r = requests.get(BASE_URL + PATH, params=params,
                     headers={"x-api-key": KEY}, timeout=60)
    if r.status_code != 200:
        print(f"\nHTTP {r.status_code} from {r.url}")
        print(f"body: {r.text[:800]}")
        sys.exit(1)
    return r.json()


def _rows(data):
    """Their response is {'snapshots': [...], 'pagination': {...}}."""
    if isinstance(data, list):
        return data
    for k in ("snapshots", "data", "orderbooks", "results"):
        if isinstance(data.get(k), list):
            return data[k]
    return []


def _cursor(data):
    pg = data.get("pagination") or {}
    for k in ("pagination_key", "next_cursor", "next", "cursor"):
        if pg.get(k):
            return pg[k]
        if data.get(k):
            return data[k]
    return None


def _touch(snap):
    """Best bid and best ask from the ladders.

    We derive them rather than trust a summary field, because this is exactly
    where the min/max flip bites: the best ask is the LOWEST ask price, and in
    Kalshi's own data that corresponds to the HIGHEST no-bid. If their yes_asks
    are already in YES terms, min() is correct.
    """
    bids = snap.get("yes_bids") or snap.get("bids") or []
    asks = snap.get("yes_asks") or snap.get("asks") or []
    f = lambda x: float(x) if x is not None else None
    bb = max((f(b["price"]) for b in bids), default=None)
    ba = min((f(a["price"]) for a in asks), default=None)
    bsz = next((f(b["size"]) for b in bids if f(b["price"]) == bb), None)
    asz = next((f(a["size"]) for a in asks if f(a["price"]) == ba), None)
    return bb, ba, bsz, asz, len(bids), len(asks)


# ------------------------------------------------------------------ probe
def probe():
    print(f"GET {BASE_URL}{PATH}")
    print(f"  ticker={REF_TICKER}  limit=20\n")
    data = _get(dict(ticker=REF_TICKER, limit=20,
                     start_time=REF_T0_MS, end_time=REF_T1_MS))
    print("top-level keys:", list(data))
    print("pagination:", json.dumps(data.get("pagination", {}))[:300])

    rows = _rows(data)
    if not rows:
        print("\nNO ROWS. Raw response:")
        print(json.dumps(data, indent=2)[:1500])
        return
    print(f"\n{len(rows)} snapshots returned.")
    print("fields on a snapshot:", list(rows[0]))

    print(f"\n{'time':>13} {'bid':>4} {'ask':>4} {'bid_sz':>9} {'ask_sz':>9} "
          f"{'nbid':>5} {'nask':>5}")
    for s in rows[:15]:
        bb, ba, bsz, asz, nb, na = _touch(s)
        ts = s.get("timestamp") or s.get("ts")
        print(f"{_iso(ts):>13} {str(bb):>4} {str(ba):>4} {str(bsz):>9} {str(asz):>9} "
              f"{nb:>5} {na:>5}")

    nb = [_touch(s)[4] for s in rows]
    na = [_touch(s)[5] for s in rows]
    print(f"\ndepth levels: bids median {sorted(nb)[len(nb)//2]}, "
          f"asks median {sorted(na)[len(na)//2]}   (we store 5)")

    ts = [s.get("timestamp") or s.get("ts") for s in rows]
    ts = sorted(t for t in ts if t)
    if len(ts) > 2:
        gaps = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
        gs = sorted(gaps)
        print(f"gaps between snapshots (ms): min {gs[0]}, median {gs[len(gs)//2]}, "
              f"max {gs[-1]}")
        print("  our median gap is 26 ms. A constant gap here would mean "
              "fixed-interval sampling, which is coarser than event-driven.")


# ---------------------------------------------------------------- compare
def fetch_window(ticker, t0_ms, t1_ms, max_requests=400):
    out, cursor, n = [], None, 0
    while n < max_requests:
        p = dict(ticker=ticker, limit=2000, start_time=t0_ms, end_time=t1_ms)
        if cursor:
            p["pagination_key"] = cursor
        data = _get(p)
        rows = _rows(data)
        out += rows
        n += 1
        cursor = _cursor(data)
        print(f"  request {n}: +{len(rows)} (total {len(out):,})", flush=True)
        if not cursor or not rows:
            break
        time.sleep(SLEEP_S)
    return out


def compare():
    import pandas as pd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from canon_load import load_book

    print(f"window {_iso(REF_T0_MS)} -> {_iso(REF_T1_MS)} UTC, {REF_TICKER}\n")
    snaps = fetch_window(REF_TICKER, REF_T0_MS, REF_T1_MS)
    if not snaps:
        sys.exit("no snapshots returned")

    recs = []
    for s in snaps:
        bb, ba, bsz, asz, nb, na = _touch(s)
        recs.append(dict(ts=s.get("timestamp") or s.get("ts"),
                         bid=bb, ask=ba, bid_size=bsz, ask_size=asz,
                         n_bid_lvl=nb, n_ask_lvl=na))
    ext = (pd.DataFrame(recs).dropna(subset=["ts"])
           .sort_values("ts").reset_index(drop=True))
    ext.to_parquet(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "canonical", "predexon_BRAMAR_BRA.parquet"),
                   index=False)

    ours = load_book(REF_MATCH, REF_LEG, columns=["bid", "ask", "bid_size", "ask_size"])

    print("\n=== RESOLUTION ===")
    for name, d in (("predexon", ext), ("ours", ours)):
        g = d.ts.diff().dropna()
        print(f"{name:9s}: {len(d):>8,} rows   median gap {g.median():>7.0f} ms   "
              f"p90 {g.quantile(.9):>8.0f} ms")
    print(f"predexon depth levels: bid median {ext.n_bid_lvl.median():.0f}, "
          f"ask median {ext.n_ask_lvl.median():.0f}   (we store 5)")

    # a crossed book is impossible; if theirs crosses, their inversion is wrong
    noninteger = ((ext.bid % 1 != 0) | (ext.ask % 1 != 0)).mean()
    print(f"non-integer touch prices: {noninteger:.3%}   "
          f"(we observed 0 of 3,000,001 trades off-tick)")
    crossed = (ext.bid >= ext.ask).mean()
    print(f"predexon crossed states: {crossed:.3%}   (ours: 0.000%)")

    m = pd.merge_asof(
        ours[["ts", "bid", "ask"]].rename(columns={"bid": "our_bid", "ask": "our_ask"}),
        ext[["ts", "bid", "ask"]].rename(columns={"bid": "ext_bid", "ask": "ext_ask"}),
        on="ts", direction="backward", tolerance=1000).dropna()

    print("\n=== AGREEMENT (on our timestamps, 1 s tolerance) ===")
    print(f"matched states : {len(m):,} of {len(ours):,}")
    if not len(m):
        sys.exit("no overlap -- check the timestamps")
    bid_eq = (m.our_bid == m.ext_bid).mean()
    ask_eq = (m.our_ask == m.ext_ask).mean()
    print(f"best bid identical : {bid_eq:.2%}")
    print(f"best ask identical : {ask_eq:.2%}")
    print(f"bid off by >1c     : {((m.our_bid - m.ext_bid).abs() > 1).mean():.2%}")
    print(f"ask off by >1c     : {((m.our_ask - m.ext_ask).abs() > 1).mean():.2%}")
    print(f"mean signed bid diff (ours - theirs): {(m.our_bid - m.ext_bid).mean():+.3f}")
    print(f"mean signed ask diff (ours - theirs): {(m.our_ask - m.ext_ask).mean():+.3f}")

    print("\n=== VERDICT ===")
    if bid_eq > 0.95 and ask_eq > 0.95:
        print("AGREES. Their archive is usable.")
        print("-> Pull the gaps: 17-21 June, and the July knockout dates.")
    elif bid_eq > 0.80:
        print("MOSTLY agrees. Likely a timing difference, not content.")
        print("-> Check the RESOLUTION block above before trusting it.")
    else:
        print("DISAGREES. Do not use either source until this is explained.")
        print("\nmismatched sample:")
        print(m[m.our_bid != m.ext_bid].head(12).to_string(index=False))


if __name__ == "__main__":
    {"probe": probe, "compare": compare}.get(
        sys.argv[1] if len(sys.argv) > 1 else "probe", probe)()
