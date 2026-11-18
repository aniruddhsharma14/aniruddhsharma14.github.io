"""
Where does Synpath's loaded window actually start?

The first probe returned 'outside_loaded_data' for every date tried, including a
late-September control. So the window is narrower than September, and the World
Cup (June-July 2026) is out of reach. Before writing Synpath off, find the real
edge: it decides whether they are the right recorder to use FROM NOW ON, which is
a live question since our own recorder missed the knockout rounds.

Two things are measured:
  * processed_through_ms -- the forward edge, straight from the response metadata
  * the backward edge     -- found by probing, then bisecting

    python3 synpath_edge.py
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timezone, timedelta

try:
    import synpath
except ImportError:
    sys.exit("pip install synpath")

NOW = int(time.time() * 1000)
HOUR, DAY = 3_600_000, 86_400_000


def iso(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


def pick_market():
    """An actively traded market, so absence means coverage and not a dead book."""
    k = synpath.Kalshi()
    best, best_v = None, -1.0
    for m in k.fetch_markets(limit=60):
        v = float(getattr(m, 'volume', 0) or 0)
        if v > best_v:
            best, best_v = m, v
    mid = str(best.id)
    return (mid if mid.startswith('kalshi:') else f'kalshi:{mid}'), best.title, best_v


def present(mid, t):
    """True if a book exists at t. None on error."""
    try:
        r = synpath.fetch_order_book_at(mid, as_of_ms=t)
        return r.book is not None, (r.absence_reason or '')
    except Exception as e:
        return None, f'{type(e).__name__}: {str(e)[:90]}'


def main():
    mid, title, vol = pick_market()
    print(f"probing: {mid}\n  {title[:70]}  (volume {vol:,.0f})\n")

    # forward edge, from metadata
    try:
        rr = synpath.fetch_order_book_range(mid, start_ms=NOW - HOUR, end_ms=NOW)
        pt = rr.metadata.processed_through_ms
        print(f"dataset_version    : {rr.metadata.dataset_version}")
        print(f"time_basis         : {rr.metadata.time_basis}")
        print(f"processed_through  : {iso(pt) + ' UTC' if pt else 'not reported'}")
        print(f"segments last hour : {[s.kind for s in rr.segments]}\n")
    except Exception as e:
        print(f"range call failed: {type(e).__name__}: {str(e)[:150]}\n")

    # coarse sweep backwards
    offsets = [0, HOUR, 6 * HOUR, DAY, 2 * DAY, 4 * DAY, 7 * DAY,
               14 * DAY, 30 * DAY, 60 * DAY, 90 * DAY, 180 * DAY]
    print(f"{'offset back':>14} {'utc':>18}   result")
    last_ok, first_bad = None, None
    for off in offsets:
        t = NOW - off
        ok, why = present(mid, t)
        lbl = 'now' if off == 0 else f'-{off // DAY}d' if off >= DAY else f'-{off // HOUR}h'
        if ok is None:
            print(f"{lbl:>14} {iso(t):>18}   ERROR {why}")
            continue
        print(f"{lbl:>14} {iso(t):>18}   {'PRESENT' if ok else 'absent  ' + why}")
        if ok:
            last_ok = t if last_ok is None or t < last_ok else last_ok
        elif first_bad is None or t > first_bad:
            first_bad = t
        time.sleep(0.25)

    # bisect between the oldest PRESENT and the newest absent older than it
    if last_ok and first_bad and first_bad < last_ok:
        lo, hi = first_bad, last_ok
        print(f"\nbisecting between {iso(lo)} and {iso(hi)} ...")
        while hi - lo > HOUR:
            mid_t = (lo + hi) // 2
            ok, _ = present(mid, mid_t)
            if ok:
                hi = mid_t
            else:
                lo = mid_t
            time.sleep(0.2)
        print(f"\nCOVERAGE STARTS between {iso(lo)} and {iso(hi)} UTC")
        print(f"  that is about {(NOW - hi) / DAY:.1f} days of history")
    elif last_ok:
        print(f"\nno absent date found as far back as {iso(NOW - offsets[-1])};"
              " coverage is deeper than the sweep")
    else:
        print("\nNo date returned a book. Either this market is not recorded,"
              " or the key lacks history access. Try another market before concluding.")


if __name__ == "__main__":
    main()
