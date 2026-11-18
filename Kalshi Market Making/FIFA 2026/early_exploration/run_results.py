"""
Regenerate the headline numbers quoted in the README, across several matches.

Run this before publishing any figures. Two earlier bugs mean previous results should not be
reused:
  1. `taker_book_side` was mapped inverted, so fills were attributed to the wrong side of
     the book and every PnL figure was wrong.
  2. The fee rate was the TAKER formula (0.07 * P * (1-P)) applied to a maker strategy.
     Set FEE_RATE from Kalshi's current maker schedule.

Usage:
    python run_results.py            # writes results.csv and prints a summary table
Edit MATCHES below to point at your data.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
import numpy as np

import backtest
from backtest import DualBook, iter_tape

# --- set this from Kalshi's published MAKER fee schedule -----------------------------
FEE_RATE = 0.07     # PLACEHOLDER: this is the taker coefficient. Replace before publishing.


def ms(y, mo, d, h, mi):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


# ticker, files, window, outcome, whether a pre-window snapshot exists
MATCHES = [
    dict(name="MEX-RSA", ticker="KXWCGAME-26JUN11MEXRSA-MEX",
         paths=["data/kalshi-2026-06-11.jsonl.gz"],
         t0=ms(2026, 6, 11, 19, 0), t1=ms(2026, 6, 11, 21, 15),
         outcome_yes=True, seed=False),
    dict(name="KOR-CZE", ticker="KXWCGAME-26JUN11KORCZE-KOR",
         paths=["data/kalshi-2026-06-11.jsonl.gz", "data/kalshi-2026-06-12.jsonl.gz"],
         t0=ms(2026, 6, 12, 2, 0), t1=ms(2026, 6, 12, 4, 15),
         outcome_yes=True, seed=True),
    dict(name="ARG-AUT", ticker="KXWCGAME-26JUN22ARGAUT-ARG",
         paths=["data/kalshi-2026-06-22.jsonl.gz"],
         t0=ms(2026, 6, 22, 17, 0), t1=ms(2026, 6, 22, 19, 30),
         outcome_yes=True, seed=True),
]


def book_states(m):
    """Reconstruct one match and return a per-book-change DataFrame."""
    book = DualBook()
    if m["seed"]:
        book.has_book = True
    rows, last = [], None
    for p in m["paths"]:
        for typ, msg, seq, ts in iter_tape(p, m["ticker"]):
            if ts is None:
                continue
            if m["t1"] and ts > m["t1"]:
                break
            if typ == "orderbook_snapshot":
                book.snapshot(msg)
            elif typ == "orderbook_delta":
                if not book.has_book:
                    continue
                book.delta(msg, seq)
            else:
                continue
            if not book.has_book or (m["t0"] and ts < m["t0"]):
                continue
            bb, ba = book.best_bid(), book.best_ask()
            if bb is None or ba is None or ba <= bb:
                continue
            bv = book.bids.get(bb, 0.0)
            av = book.asks.get(ba, 0.0)
            key = (bb, ba, bv, av)
            if key == last:
                continue
            last = key
            rows.append(dict(ts=ts, best_bid=bb, best_ask=ba, spread=ba - bb,
                             mid=(bb + ba) / 2, bid_sz=bv, ask_sz=av))
    df = pd.DataFrame(rows)
    if len(df):
        df["secs"] = (df.ts - df.ts.iloc[0]) / 1000.0
    return df


def summarise(name, df):
    if not len(df):
        return dict(match=name, states=0)
    d = df[df.spread >= 1]
    p = d['mid'].values
    t = d.secs.values
    chg = np.flatnonzero(np.diff(p) != 0) + 1
    moves = np.abs(np.diff(p)[chg - 1]) if len(chg) else np.array([])
    gaps = np.diff(t[chg]) if len(chg) > 1 else np.array([])
    return dict(
        match=name,
        states=len(d),
        minutes=round(d.secs.iloc[-1] / 60, 1),
        spread_1tick=round((d.spread == 1).mean(), 4),
        spread_mean=round(d.spread.mean(), 3),
        bid_sz_median=round(d.bid_sz.median(), 1),
        ask_sz_median=round(d.ask_sz.median(), 1),
        touch_changes=len(chg),
        frac_1tick_move=round((moves <= 1).mean(), 4) if len(moves) else np.nan,
        median_gap_ms=round(np.median(gaps) * 1000, 1) if len(gaps) else np.nan,
        mid_start=p[0] if len(p) else np.nan,
        mid_end=p[-1] if len(p) else np.nan,
    )


if __name__ == "__main__":
    out = []
    for m in MATCHES:
        print(f"\n=== {m['name']} ===")
        try:
            df = book_states(m)
            s = summarise(m["name"], df)
            print(f"  {s['states']} states over {s.get('minutes','?')} min")
            out.append(s)
        except Exception as e:
            print(f"  FAILED: {e}")
    res = pd.DataFrame(out)
    res.to_csv("results.csv", index=False)
    print("\n" + "=" * 78)
    print(res.to_string(index=False))
    print("=" * 78)
    print("\nwrote results.csv")
    print("\nNOTE: FEE_RATE is still the taker coefficient. Set it from Kalshi's maker")
    print("schedule before quoting any PnL figure publicly.")
