"""
Synpath coverage probe -- does their free Kalshi tick archive reach June/July 2026?

This is the only question that matters right now. Synpath's design is better than
anything else we found:

  * each range segment carries a FULL initial book plus the exact deltas
    (`price_exact`, `quantity_delta_exact`) -- no seeding problem AND no loss
  * gaps are explicit `absent` segments with a reason, never an empty book
  * both `observed_at_ms` (recorder) and `venue_timestamp_ms` (exchange)
  * free, MIT, pip install synpath

But every example in their repo and README uses late September 2026, and no page
states a coverage start date. If recording began in September, they have no World
Cup data at all and none of the above helps us.

So: ask.

    pip install synpath
    python3 synpath_probe.py

Needs a Synpath API key. `synpath keys create` saves one and the library picks it
up; otherwise export SYNPATH_API_KEY.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone

try:
    import synpath
except ImportError:
    sys.exit("pip install synpath")


def ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def iso(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


# Dates chosen to separate the three possibilities. Each is a real moment when
# that market was actively trading, so an absence is about coverage, not liquidity.
PROBES = [
    ("BRA-MAR kickoff  (we HOLD this -- reference)",
     "kalshi:KXWCGAME-26JUN13BRAMAR-BRA", ms(2026, 6, 13, 22, 0)),
    ("KOR-CZE kickoff  (we hold this too)",
     "kalshi:KXWCGAME-26JUN11KORCZE-KOR", ms(2026, 6, 12, 2, 0)),
    ("MEX-KOR 18 Jun   (OUR GAP: 17-21 June never recorded)",
     "kalshi:KXWCGAME-26JUN18MEXKOR-MEX", ms(2026, 6, 18, 20, 0)),
    ("CZE-RSA 18 Jun   (OUR GAP)",
     "kalshi:KXWCGAME-26JUN18CZERSA-CZE", ms(2026, 6, 18, 17, 0)),
    ("late Sept 2026   (their own examples use this era)",
     None, ms(2026, 9, 25, 18, 0)),
]


def describe(market_id, t):
    try:
        r = synpath.fetch_order_book_at(market_id, as_of_ms=t)
    except Exception as e:
        return f"ERROR  {type(e).__name__}: {str(e)[:120]}"
    if r.book is None:
        return f"ABSENT reason={r.absence_reason!r}"
    b = r.book
    bb = b.bids[0].price if b.bids else None
    ba = b.asks[0].price if b.asks else None
    return (f"PRESENT bid={bb} ask={ba} levels={len(b.bids)}/{len(b.asks)} "
            f"depth_scope={getattr(b, 'depth_scope', '?')} "
            f"venue_ts={getattr(b, 'venue_timestamp_ms', None)}")


def main():
    print("Synpath history coverage probe\n" + "=" * 78)

    # a currently-live market first: proves the key and the service work, so an
    # absence on a June date means "no coverage" and not "broken setup"
    live_id = None
    try:
        k = synpath.Kalshi()
        mk = k.fetch_markets(limit=1)
        if mk:
            live_id = f"kalshi:{mk[0].id}" if not str(mk[0].id).startswith("kalshi:") else mk[0].id
            print(f"\nlive market for the control check: {live_id}")
    except Exception as e:
        print(f"could not fetch a live market ({type(e).__name__}); control check skipped")

    for label, market_id, t in PROBES:
        mid = market_id or live_id
        if mid is None:
            continue
        print(f"\n{label}\n  {mid}  @ {iso(t)} UTC")
        print(f"  -> {describe(mid, t)}")

    # If June is covered, confirm a range call returns deltas, not just snapshots.
    print("\n" + "=" * 78)
    print("range check on BRA-MAR (1 minute from kickoff):")
    try:
        t0 = ms(2026, 6, 13, 22, 0)
        rr = synpath.fetch_order_book_range(
            "kalshi:KXWCGAME-26JUN13BRAMAR-BRA", start_ms=t0, end_ms=t0 + 60_000)
        print(f"  dataset_version={rr.metadata.dataset_version} "
              f"time_basis={rr.metadata.time_basis}")
        print(f"  segments={len(rr.segments)}")
        for s in rr.segments[:4]:
            if s.kind == "absent":
                print(f"    absent {iso(s.start_ms)}->{iso(s.end_ms)} reason={s.reason!r}")
            else:
                ib = s.initial_book
                kinds = {}
                for c in s.changes:
                    kinds[c.kind] = kinds.get(c.kind, 0) + 1
                print(f"    data   initial_book levels="
                      f"{len(ib.bids) if ib else 0}/{len(ib.asks) if ib else 0} "
                      f"changes={len(s.changes)} kinds={kinds}")
                if s.changes:
                    c = s.changes[0]
                    print(f"           first change: kind={c.kind} side={c.book_side} "
                          f"price_exact={c.price_exact} qty_delta_exact={c.quantity_delta_exact}")
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {str(e)[:200]}")

    print("\n" + "=" * 78)
    print("READING THE RESULT")
    print("  June PRESENT  -> they have the World Cup. Best source found; it gives a")
    print("                   full initial book AND exact deltas, free.")
    print("  June ABSENT   -> recording started later. Useless for the 2026 World Cup,")
    print("                   but the right tool for recording from now on.")
    print("  Sept PRESENT, June ABSENT -> confirms it is a coverage start date,")
    print("                   not a broken key or a wrong ticker.")


if __name__ == "__main__":
    main()
