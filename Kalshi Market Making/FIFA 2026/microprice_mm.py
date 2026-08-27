"""
MICROPRICE-CENTERED MARKET MAKER with a DYNAMIC REPRICE THRESHOLD.

Strategy:
  - Center quotes on the MICROPRICE (volume-weighted). Quote one level each side at a fixed
    offset from it.
  - HYSTERESIS: don't move quotes on every tiny wiggle. Only re-quote when the microprice has
    drifted >= x cents from where we last quoted.
  - DYNAMIC x: x adapts to recent microprice volatility. Jumpy market -> wider x (don't churn
    and lose queue position); calm market -> tighter x (stay near fair value).
        x = clamp( k * recent_volatility, x_min, x_max )
  - Basic inventory limit so a one-sided run doesn't bury us.

This is NOT an alpha strategy -- it isolates the CHURN vs STALENESS trade-off. Metrics that
matter: cancel/fill ratio (churn), and PnL after fees. The hypothesis is that dynamic x finds
a better churn/staleness point than any fixed x.

This runs MICROPRICE DEPTH = 1, 3, and 5 in a single pass so you can compare which depth
weighting works best, and includes a FIXED-x baseline for each depth.

    python3 microprice_mm.py            # edit run config at the bottom
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import json

import backtest
from backtest import DualBook, iter_tape, _c
from shadow_trader import QueueAwareFillModel


def microprice(book: DualBook, levels: int):
    """Volume-weighted price: touch prices weighted by top-`levels` depth on each side."""
    bb, ba = book.best_bid(), book.best_ask()
    if bb is None or ba is None:
        return None
    tb = sorted((p for p in book.bids if book.bids[p] >= 0.5), reverse=True)[:levels]
    ta = sorted((p for p in book.asks if book.asks[p] >= 0.5))[:levels]
    bv = sum(book.bids[p] for p in tb)
    av = sum(book.asks[p] for p in ta)
    if bv + av <= 0:
        return (bb + ba) / 2.0
    return (bb * av + ba * bv) / (bv + av)


@dataclass
class MPConfig:
    levels: int = 3                  # microprice depth (1, 3, 5, ...)
    offset: float = 1.0              # quote this many cents each side of microprice
    size: int = 1
    inv_limit: int = 30
    fee_rate: float = 0.07           # set to Kalshi MAKER schedule for true PnL

    # reprice threshold x
    dynamic_x: bool = True           # True = x adapts to volatility; False = fixed x_min
    x_k: float = 5.0                 # churn dial: reprice after microprice moves x_k * vol.
                                     # (raised from 1.5 -- at 1.5 x pinned at the floor on
                                     #  real data because per-second microprice vol is small)
    x_min: float = 0.5               # clamp floor, lowered so x has room to vary
    x_max: float = 4.0
    vol_secs: float = 10.0           # TIME window (seconds) for the volatility estimate


class MicropriceMM:
    def __init__(self, cfg: MPConfig):
        self.cfg = cfg
        self.fills = QueueAwareFillModel(fee_rate=cfg.fee_rate)
        self.mp_hist = deque()                             # (ts_sec, microprice) time-windowed
        self.last_quote_mp = None                          # microprice at last re-quote
        self.reprices = 0
        self.book_events = 0
        self.x_samples = []                                # every x we computed (to inspect)
        self.quote_log = []                                # every reprice: market state + our quotes

    def _recent_vol(self):
        """Volatility over the last vol_secs SECONDS: mean absolute microprice change per
        second. Time-based (not event-based) so it means the same thing during a goal burst
        (~100 events/s) and a quiet stretch (~1 event/s)."""
        if len(self.mp_hist) < 3:
            return self.cfg.x_min
        span = self.mp_hist[-1][0] - self.mp_hist[0][0]
        if span <= 0:
            return self.cfg.x_min
        total_move = sum(abs(self.mp_hist[i][1] - self.mp_hist[i - 1][1])
                         for i in range(1, len(self.mp_hist)))
        return total_move / span          # cents of microprice movement per second

    def _threshold(self):
        if not self.cfg.dynamic_x:
            return self.cfg.x_min
        x = self.cfg.x_k * self._recent_vol()
        return max(self.cfg.x_min, min(self.cfg.x_max, x))

    def on_trade(self, price, size, aggressor):
        self.fills.on_trade(int(price), int(round(size)), aggressor)

    def on_book_event(self, book: DualBook, now):
        mp = microprice(book, self.cfg.levels)
        if mp is None:
            return
        self.mp_hist.append((now, mp))
        cutoff = now - self.cfg.vol_secs
        while self.mp_hist and self.mp_hist[0][0] < cutoff:
            self.mp_hist.popleft()
        self.book_events += 1

        # decide whether to re-quote: first time, or microprice drifted past threshold
        x = self._threshold()
        self.x_samples.append(x)
        if self.last_quote_mp is None or abs(mp - self.last_quote_mp) >= x:
            self._requote(book, mp, now, x)
            self.last_quote_mp = mp

    def _requote(self, book: DualBook, mp, now=None, x=None):
        self.reprices += 1
        bb, ba = book.best_bid(), book.best_ask()
        inv = self.fills.inventory
        # desired one level each side, integer cents, never crossing the book
        want_bid = int(round(mp - self.cfg.offset))
        want_ask = int(round(mp + self.cfg.offset))

        # bid
        if inv < self.cfg.inv_limit and 1 <= want_bid < (ba if ba else 100):
            self._set("bid", want_bid, book)
        else:
            self.fills.cancel("bid") if self.fills.orders.get("bid") else None
        # ask
        if inv > -self.cfg.inv_limit and (bb if bb else 0) < want_ask <= 99:
            self._set("ask", want_ask, book)
        else:
            self.fills.cancel("ask") if self.fills.orders.get("ask") else None

        # --- record what we quoted, against the market state, with queue positions ---
        ob, oa = self.fills.orders.get("bid"), self.fills.orders.get("ask")
        self.quote_log.append(dict(
            ts=now, x=round(x, 2) if x is not None else None,
            mkt_bid=bb, mkt_ask=ba,
            mkt_spread=(ba - bb) if (bb is not None and ba is not None) else None,
            microprice=round(mp, 2),
            our_bid=ob.price if ob else None,
            our_ask=oa.price if oa else None,
            # queue_ahead = resting volume in front of us at that level when we joined.
            # We only start filling once this reaches 0.
            bid_queue_ahead=ob.queue_ahead if ob else None,
            ask_queue_ahead=oa.queue_ahead if oa else None,
            # is our quote at/inside the touch, or behind it?
            bid_vs_touch=(ob.price - bb) if (ob and bb is not None) else None,
            ask_vs_touch=(oa.price - ba) if (oa and ba is not None) else None,
            inventory=inv, fills_so_far=self.fills.fills,
        ))

    def _set(self, side, price, book):
        cur = self.fills.orders.get(side)
        qa = int(book.bids.get(price, 0) if side == "bid" else book.asks.get(price, 0))
        if cur is None:
            self.fills.place(side, price, qa, self.cfg.size)
        elif cur.price != price:
            self.fills.cancel(side)
            self.fills.place(side, price, qa, self.cfg.size)
        # else keep queue position


def run_one(paths, ticker, outcome_yes, cfg: MPConfig, _return_mm=False,
            t0_ms=None, t1_ms=None, seed_from_deltas=False, warmup_ms=None):
    if isinstance(paths, str):
        paths = [paths]
    book = DualBook()
    if seed_from_deltas:
        book.has_book = True
    mm = MicropriceMM(cfg)
    first_ts = None; mids = []
    for p in paths:
        for typ, msg, seq, ts in iter_tape(p, ticker):
            if first_ts is None and ts: first_ts = ts
            if t1_ms and ts and ts > t1_ms: break
            # BUILD the book from ALL events (incl. the pre-window seeding snapshot);
            # only ACT (quote/fill) once inside [t0, t1]. Skipping pre-window events
            # would drop the snapshot and the book would never initialize.
            in_window = (t0_ms is None or (ts and ts >= t0_ms))
            warm = (warmup_ms is None or first_ts is None or ts is None
                    or (ts - first_ts) >= warmup_ms)
            if typ == "orderbook_snapshot":
                book.snapshot(msg)
            elif typ == "orderbook_delta":
                if not book.has_book: continue
                book.delta(msg, seq)
            elif typ == "trade":
                if not book.has_book or not in_window: continue
                price = _c(msg["yes_price_dollars"]); size = float(msg.get("count_fp", 0))
                # bid == yes: taker bought YES -> aggressive buy. See Kalshi API docs.
                agg = "buy" if msg.get("taker_book_side") == "bid" else "sell"
                mm.fills.ctx_ts = ts; mm.fills.ctx_mid = book.mid()
                mm.on_trade(price, size, agg)
            if (typ in ("orderbook_snapshot", "orderbook_delta")
                    and book.has_book and warm and in_window):
                m = book.mid()
                if m is not None:
                    mids.append(m)
                    mm.on_book_event(book, (ts or 0) / 1000.0)
    pnl = mm.fills.settle(outcome_yes)
    cf = mm.fills.cancels / max(mm.fills.fills, 1)
    xs = mm.x_samples
    if xs:
        x_mean = sum(xs) / len(xs)
        x_lo = sum(1 for v in xs if v <= cfg.x_min + 1e-9) / len(xs)   # frac pinned at min
        x_hi = sum(1 for v in xs if v >= cfg.x_max - 1e-9) / len(xs)   # frac pinned at max
    else:
        x_mean = x_lo = x_hi = 0.0
    out = dict(
        levels=cfg.levels, dynamic_x=cfg.dynamic_x,
        settled_pnl=round(pnl, 2), fills=mm.fills.fills, cancels=mm.fills.cancels,
        cancel_per_fill=round(cf, 1), fees=round(mm.fills.fees, 2),
        reprices=mm.reprices, book_events=mm.book_events,
        reprice_rate=round(mm.reprices / max(mm.book_events, 1), 3),
        final_inv=mm.fills.inventory,
        x_min_seen=round(min(xs), 2) if xs else None,
        x_mean=round(x_mean, 2), x_max_seen=round(max(xs), 2) if xs else None,
        x_frac_at_floor=round(x_lo, 2), x_frac_at_ceil=round(x_hi, 2),
        mid_start=round(mids[0], 1) if mids else None,
        mid_end=round(mids[-1], 1) if mids else None,
    )
    return (out, mm) if _return_mm else out


def inspect_quotes(paths, ticker, outcome_yes, cfg: MPConfig = None,
                   n_show=40, csv_path=None, **kw):
    """Run ONE config and show every quote: market state, our prices, queue positions.
    This is the diagnostic for 'why aren't we filling' -- look at bid/ask_vs_touch
    (are we at the touch or behind it?) and queue_ahead (how much size is in front)."""
    cfg = cfg or MPConfig()
    r, mm = run_one(paths, ticker, outcome_yes, cfg, _return_mm=True, **kw)
    log = mm.quote_log
    print(f"\n{'='*100}")
    print(f"QUOTE LOG  depth={cfg.levels} volsec={cfg.vol_secs} "
          f"{'dynamic' if cfg.dynamic_x else 'fixed'}-x  offset={cfg.offset}")
    print(f"total quotes={len(log)}  fills={r['fills']}  cancels={r['cancels']}")
    print(f"{'='*100}")
    hdr = (f"{'ts':>10} {'x':>5} {'mktbid':>7} {'mktask':>7} {'spr':>4} {'micro':>7} "
           f"{'ourbid':>7} {'ourask':>7} {'bidQ':>7} {'askQ':>7} "
           f"{'b-touch':>8} {'a-touch':>8} {'inv':>5} {'fills':>6}")
    print(hdr); print("-" * len(hdr))
    for q in log[:n_show]:
        print(f"{(q['ts'] or 0)%100000:>10.0f} {str(q['x']):>5} "
              f"{str(q['mkt_bid']):>7} {str(q['mkt_ask']):>7} {str(q['mkt_spread']):>4} "
              f"{q['microprice']:>7} {str(q['our_bid']):>7} {str(q['our_ask']):>7} "
              f"{str(q['bid_queue_ahead']):>7} {str(q['ask_queue_ahead']):>7} "
              f"{str(q['bid_vs_touch']):>8} {str(q['ask_vs_touch']):>8} "
              f"{q['inventory']:>5} {q['fills_so_far']:>6}")
    if len(log) > n_show:
        print(f"... ({len(log)-n_show} more)")

    # aggregate queue diagnostics -- the real answer to 'why don't we fill'
    bq = [q['bid_queue_ahead'] for q in log if q['bid_queue_ahead'] is not None]
    aq = [q['ask_queue_ahead'] for q in log if q['ask_queue_ahead'] is not None]
    bt = [q['bid_vs_touch'] for q in log if q['bid_vs_touch'] is not None]
    at = [q['ask_vs_touch'] for q in log if q['ask_vs_touch'] is not None]
    def stats(v, name):
        if not v: print(f"  {name}: (none)"); return
        v2 = sorted(v)
        print(f"  {name}: mean {sum(v)/len(v):>8.1f}  median {v2[len(v2)//2]:>7.1f}  "
              f"frac_zero {sum(1 for z in v if z == 0)/len(v):>5.2f}")
    print(f"\nQUEUE POSITION (size resting ahead of us when we joined; we fill only after it clears):")
    stats(bq, "bid queue_ahead"); stats(aq, "ask queue_ahead")
    print(f"\nQUOTE PLACEMENT (0 = at the touch, negative bid = behind best bid,"
          f" positive ask = behind best ask):")
    stats(bt, "our_bid - mkt_bid"); stats(at, "our_ask - mkt_ask")
    print("\nIf queue_ahead is large and rarely 0, we're stuck behind size and won't fill.")
    print("If b-touch is very negative / a-touch very positive, we're quoting away from the")
    print("market and will only fill on big moves (i.e. adversely selected).")

    if csv_path:
        import csv as _csv
        with open(csv_path, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(log[0].keys()))
            w.writeheader(); w.writerows(log)
        print(f"\nfull quote log -> {csv_path}  ({len(log)} rows)")
    return log, r


def sweep(paths, ticker, outcome_yes, base: MPConfig = None, **kw):
    """Run microprice depth 1/3/5 x volatility window 5/10/15s, dynamic AND fixed x."""
    base = base or MPConfig()
    print(f"{'depth':>5} {'volsec':>6} {'x-mode':>8} {'pnl':>8} {'fills':>6} {'canc/fill':>9} "
          f"{'reprices':>8} {'x_mean':>7} {'x_rng':>11} {'%floor':>7} {'inv':>5}")
    print("-" * 92)
    rows = []
    for levels in (1, 3, 5):
        for vs in (5.0, 10.0, 15.0):
            for dyn in (True, False):
                cfg = MPConfig(**{**base.__dict__, "levels": levels,
                                  "vol_secs": vs, "dynamic_x": dyn})
                r = run_one(paths, ticker, outcome_yes, cfg, **kw)
                r["vol_secs"] = vs
                rows.append(r)
                xrng = f"{r['x_min_seen']}-{r['x_max_seen']}"
                print(f"{levels:>5} {vs:>6.0f} {'dyn' if dyn else 'fixed':>8} "
                      f"{r['settled_pnl']:>8} {r['fills']:>6} {r['cancel_per_fill']:>9} "
                      f"{r['reprices']:>8} {r['x_mean']:>7} {xrng:>11} "
                      f"{r['x_frac_at_floor']:>7} {r['final_inv']:>5}")
        print()
    print("Read: x_mean/x_rng/%floor show whether dynamic x ADAPTED or sat pinned at x_min.")
    print("x_k is the churn dial: higher = more patient (fewer reprices, staler quotes).")
    print("Lower canc/fill = less churn. PnL settled, net of fees. (Single match -> indicative.)")
    return rows


if __name__ == "__main__":
    from datetime import datetime, timezone
    def ms(y, mo, d, h, mi):
        return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)

    # Both June 11 matches, each with its correct file(s), window, seed mode, and outcome.
    # MEX-RSA: clean snapshot, Mexico won 2-0 -> -MEX settles YES. Kickoff 19:00 UTC.
    # KOR-CZE: no snapshot (seed-from-deltas), Korea won 2-1 -> -KOR settles YES.
    #          Kickoff 10PM ET = 02:00 UTC June 12 (crosses midnight).
    MATCHES = [
        dict(name="MEX-RSA (clean snapshot)",
             paths="data/kalshi-2026-06-11.jsonl.gz",
             ticker="KXWCGAME-26JUN11MEXRSA-MEX",
             outcome_yes=True, seed_from_deltas=False,
             t0_ms=ms(2026, 6, 11, 19, 0), t1_ms=ms(2026, 6, 11, 21, 15)),
        dict(name="KOR-CZE (seed-from-deltas -- rougher reconstruction)",
             paths="data/kalshi-2026-06-12.jsonl.gz",
             ticker="KXWCGAME-26JUN11KORCZE-KOR",
             outcome_yes=True, seed_from_deltas=True, warmup_ms=1800000,
             t0_ms=ms(2026, 6, 12, 2, 0), t1_ms=ms(2026, 6, 12, 4, 15)),
    ]
    for m in MATCHES:
        print(f"\n{'#'*70}\n# {m['name']}\n#   {m['ticker']}\n{'#'*70}")
        kw = {k: m[k] for k in ("t0_ms", "t1_ms", "seed_from_deltas") if k in m}
        if "warmup_ms" in m: kw["warmup_ms"] = m["warmup_ms"]
        sweep(m["paths"], m["ticker"], m["outcome_yes"], base=MPConfig(), **kw)
