"""
Backtest the CJP strategy on the recorded Game-3 tape (queue-aware fills).

Reads the recorder's JSONL (envelope with stringified `raw` Kalshi ws message),
filters to ONE market ticker, reconstructs the unified YES-terms L2 order book from
the orderbook_snapshot + orderbook_delta stream, and replays it through the same
QueueAwareFillModel / ShadowTrader used live -- so backtest and live agree.

Kalshi specifics handled here:
  * YES and NO are separate books; a NO level at price p == a YES level at (100 - p).
    Everything is normalised to YES (here: NYK win-probability) terms.
  * deltas are SIGNED changes to a price level's resting size; we maintain a running ladder.
  * seq gaps (dropped messages) and negative rung sizes are COUNTED and reported --
    if these are large, the reconstructed depth (and the queue-aware fills) are untrustworthy.

Settlement: pass outcome_yes=False for the NYK market (Knicks LOST Game 3 -> NO/0).
"""
from __future__ import annotations
import json
from shadow_trader import ShadowTrader, ShadowConfig
from cjp_market_maker import OptimalMM, MMConfig


def _c(price_dollars):           # "0.4300" -> 43 (cents, int)
    return int(round(float(price_dollars) * 100))


class BookReconstructor:
    """Maintains a unified YES-terms ladder from snapshot + delta messages."""
    def __init__(self):
        self.yes: dict[int, float] = {}     # yes-price(cents) -> resting size
        self.expected_seq = None
        self.seq_gaps = 0
        self.neg_clamps = 0
        self.has_book = False

    def _yes_price(self, side, price_cents):
        # NO level at p  <=>  YES level at (100 - p)
        return price_cents if side == "yes" else 100 - price_cents

    def apply_snapshot(self, msg):
        self.yes.clear()
        for p, s in msg.get("yes_dollars_fp", []):
            self.yes[_c(p)] = self.yes.get(_c(p), 0.0) + float(s)
        for p, s in msg.get("no_dollars_fp", []):
            yp = 100 - _c(p)
            self.yes[yp] = self.yes.get(yp, 0.0) + float(s)
        self.expected_seq = msg_seq = None  # snapshot resets the sequence baseline
        self.has_book = True

    def apply_delta(self, msg, seq):
        if self.expected_seq is not None and seq != self.expected_seq:
            self.seq_gaps += 1                 # dropped or out-of-order message
        self.expected_seq = (seq + 1) if seq is not None else None
        yp = self._yes_price(msg["side"], _c(msg["price_dollars"]))
        new = self.yes.get(yp, 0.0) + float(msg["delta_fp"])
        if new < 0:
            self.neg_clamps += 1               # impossible in a real book -> desync symptom
            new = 0.0
        if new == 0:
            self.yes.pop(yp, None)
        else:
            self.yes[yp] = new

    def book_for_trader(self):
        """Split the unified YES ladder back into (bids, asks) around the mid for the trader.
        A YES level is a bid if below mid, an ask if above. We infer mid from the crossing
        point: highest price with 'bid-like' resting size vs lowest 'ask-like'. Kalshi's two
        books already encode this -- yes-book entries are bids, inverted no-book entries are
        asks -- but after merging we reseparate by tracking origin."""
        return self.yes


def _open_tape(path):
    """Open a tape file, transparently handling gzip (.gz) or plain text."""
    import gzip
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt")     # 'rt' = decompress + decode to text lines
    return open(path)


def iter_tape(path, ticker):
    """Yield ('snapshot'|'delta'|'trade', msg, seq, ts_ms) for ONE ticker, in file order."""
    with _open_tape(path) as f:
        for line in f:
            try:
                env = json.loads(line)
                raw = json.loads(env["raw"])
            except Exception:
                continue
            typ = raw.get("type")
            if typ not in ("orderbook_snapshot", "orderbook_delta", "trade"):
                continue
            msg = raw.get("msg", {})
            if msg.get("market_ticker") != ticker:
                continue
            yield typ, msg, raw.get("seq"), env.get("ts_recv_ms")


# NOTE: the snapshot gives separate yes/no books, which directly tells us bids vs asks.
# To keep that distinction cleanly we maintain TWO ladders rather than one merged dict.
class DualBook:
    def __init__(self):
        self.bids: dict[int, float] = {}      # yes bids: yes-price -> size
        self.asks: dict[int, float] = {}      # yes asks: yes-price -> size (from inverted no-book)
        self.expected_seq = None
        self.seq_gaps = 0; self.neg_clamps = 0; self.has_book = False

    def snapshot(self, msg):
        yes = msg.get("yes_dollars_fp") or []
        no = msg.get("no_dollars_fp") or []
        if not yes and not no:
            return                       # empty snapshot (heartbeat/ack) -> NOT a reset; ignore
        self.bids.clear(); self.asks.clear()
        for p, s in yes:
            self.bids[_c(p)] = float(s)                 # resting YES buy orders = bids
        for p, s in no:
            self.asks[100 - _c(p)] = float(s)           # resting NO buy = YES sell = asks
        self.expected_seq = None; self.has_book = True

    def delta(self, msg, seq):
        # Kalshi's `seq` is a GLOBAL counter shared across all channels/markets on the
        # connection, so this market's deltas are a forward-skipping subsequence (3,6,9..).
        # A real gap/out-of-order is only seq <= last_seq (backward or repeat), NOT a
        # forward jump (those are just other channels' messages interleaved).
        if self.expected_seq is not None and seq is not None and seq <= self.expected_seq:
            self.seq_gaps += 1
        if seq is not None:
            self.expected_seq = seq
        pc = _c(msg["price_dollars"]); d = float(msg["delta_fp"])
        if msg["side"] == "yes":
            book, key = self.bids, pc
        else:
            book, key = self.asks, 100 - pc
        new = book.get(key, 0.0) + d
        if new < 0: self.neg_clamps += 1; new = 0.0
        if new < 0.5:                 # below half a contract = empty; float residue from
            book.pop(key, None)       # netting (e.g. +98873.6 - 98873.6 != exactly 0) would
        else:                         # otherwise leave dust that poisons best_bid/best_ask
            book[key] = new

    def best_bid(self):
        live = [p for p, s in self.bids.items() if s >= 0.5]
        return max(live) if live else None
    def best_ask(self):
        live = [p for p, s in self.asks.items() if s >= 0.5]
        return min(live) if live else None
    def mid(self):
        b, a = self.best_bid(), self.best_ask()
        return (b + a) / 2.0 if (b is not None and a is not None) else None


def run_backtest(paths, ticker, outcome_yes, mm_cfg=None, scfg=None,
                 t0_ms=None, t1_ms=None, settle_epoch_ms=None,
                 seed_from_deltas=False, warmup_ms=None, verbose=True):
    # accept a single path or a list of files to replay IN ORDER (one continuous book).
    # Needed when the seeding snapshot is in an earlier file (e.g. pre-game June 8) and
    # the game plays out in a later file (June 9) that contains deltas only.
    #
    # seed_from_deltas=True: for recordings whose ONLY populated snapshot is at connect time
    # (days earlier / in another file). Instead of replaying days of deltas (slow + carries
    # drift from any dropped message), start the book EMPTY and let this file's own deltas
    # rebuild it. Every actively-traded level becomes correct within minutes; only levels
    # never touched during the window stay unknown. Use warmup_ms to ignore the first N ms
    # of (still-filling) book before trusting the mid / trading.
    if isinstance(paths, str):
        paths = [paths]
    book = DualBook()
    if seed_from_deltas:
        book.has_book = True          # accept deltas immediately, building from empty
    mm = OptimalMM(mm_cfg or MMConfig())
    st = ShadowTrader(mm, scfg or ShadowConfig(log_path="/tmp/backtest_log.jsonl"))
    # settlement time (epoch SECONDS, matching on_book_event's now=ts/1000) so the engine's
    # time-to-settle counts DOWN across the game and terminal flattening (alpha_term) engages.
    # Auto-detect from the last event if not supplied: scan once for the max timestamp.
    if settle_epoch_ms is None:
        last = 0
        for _, _, _, ts in (ev for p in paths for ev in iter_tape(p, ticker)):
            if ts and ts > last: last = ts
        settle_epoch_ms = last
    st.scfg.settle_epoch = settle_epoch_ms / 1000.0
    mids = []
    n_book = n_trade = 0
    first_ts = [None]
    def events():
        for p in paths:
            for ev in iter_tape(p, ticker):
                yield ev
    for typ, msg, seq, ts in events():
        if first_ts[0] is None and ts: first_ts[0] = ts
        warm = (warmup_ms is None or first_ts[0] is None or ts is None
                or (ts - first_ts[0]) >= warmup_ms)   # has the from-empty book filled in?
        if t0_ms and ts and ts < t0_ms: continue
        if t1_ms and ts and ts > t1_ms: continue
        if typ == "orderbook_snapshot":
            book.snapshot(msg)
        elif typ == "orderbook_delta":
            if not book.has_book: continue
            book.delta(msg, seq)
        elif typ == "trade":
            if not book.has_book: continue
            price = _c(msg["yes_price_dollars"])
            size = float(msg.get("count_fp", 0))
            # aggressor in YES terms: taker hitting the YES bid is an aggressive SELL of YES
            # Kalshi docs: book_side bid == yes, ask == no. taker_book_side names the
            # OUTCOME the taker bought. Bought YES -> aggressive buy of YES;
            # bought NO  -> economically an aggressive sell of YES.
            agg = "buy" if msg.get("taker_book_side") == "bid" else "sell"
            st.fills.ctx_ts = ts; st.fills.ctx_mid = book.mid()   # context for the trade log
            st.sig.on_trade(agg)
            st.fills.on_trade(int(price), int(round(size)), agg)
            n_trade += 1
        # drive the trader on book updates (only after warmup, once the book has filled in)
        if typ in ("orderbook_snapshot", "orderbook_delta") and book.has_book and warm:
            mid = book.mid()
            if mid is not None:
                n_book += 1
                mids.append((ts, mid))
                st.on_book_event(book.bids, book.asks, (ts or 0) / 1000.0)
    pnl = st.fills.settle(outcome_yes)
    # write the sequential trade log (every fill, in order, with context)
    tl = st.fills.trade_log
    import csv
    with open("backtest_trades.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "ts_ms", "side", "price_c", "qty", "mid_c", "inventory", "cash_$"])
        for i, r in enumerate(tl):
            w.writerow([i, r["ts"], r["side"], r["price"], r["qty"],
                        r["mid"], r["inventory"], r["cash"]])
    midvals = [m for _, m in mids]
    summary = dict(st.summary(), settled_pnl=round(pnl, 2),
                   seq_gaps=book.seq_gaps, neg_clamps=book.neg_clamps,
                   book_updates=n_book, trades=n_trade, valid_mids=len(midvals),
                   mid_start=round(midvals[0], 1) if midvals else None,
                   mid_end=round(midvals[-1], 1) if midvals else None,
                   mid_min=round(min(midvals), 1) if midvals else None,
                   mid_max=round(max(midvals), 1) if midvals else None,
                   mid_quarters=[round(midvals[int(len(midvals)*f)], 1)
                                 for f in (0.25, 0.5, 0.75)] if midvals else None)
    if verbose:
        print(json.dumps(summary, indent=2))
        print(f"\nwrote {len(tl)} fills to backtest_trades.csv")
    return summary, mids, tl


if __name__ == "__main__":
    # ---- self-test: synthetic tape with the exact record shapes + a dropped delta ----
    import tempfile, os
    recs = []
    def env(raw): return json.dumps({"ts_recv_ms": raw["_ts"], "venue": "kalshi",
                                     "raw": json.dumps({k: v for k, v in raw.items() if k != "_ts"})})
    # snapshot: yes bids near 52, no bids near 47 (-> yes asks near 53)
    recs.append(env({"_ts": 1000, "type": "orderbook_snapshot", "seq": 1,
                     "msg": {"market_ticker": "T", "yes_dollars_fp": [["0.5200", "500"], ["0.5100", "800"]],
                             "no_dollars_fp": [["0.4700", "600"], ["0.4600", "900"]]}}))
    # valid delta seq6 (forward jump from snapshot is NORMAL: other channels use the
    # shared global counter), then a BACKWARD seq3 -> that's the real gap (1 expected).
    recs.append(env({"_ts": 1100, "type": "orderbook_delta", "seq": 6,
                     "msg": {"market_ticker": "T", "price_dollars": "0.5200", "delta_fp": "100", "side": "yes"}}))
    recs.append(env({"_ts": 1200, "type": "orderbook_delta", "seq": 3,
                     "msg": {"market_ticker": "T", "price_dollars": "0.5200", "delta_fp": "-9999", "side": "yes"}}))
    # a trade
    recs.append(env({"_ts": 1300, "type": "trade",
                     "msg": {"market_ticker": "T", "yes_price_dollars": "0.5300", "count_fp": "10",
                             "taker_side": "yes", "taker_book_side": "ask"}}))
    fp = tempfile.mktemp(suffix=".jsonl")
    open(fp, "w").write("\n".join(recs))
    s, _, _ = run_backtest(fp, "T", outcome_yes=False)
    s2, _, _ = run_backtest(fp, "T", outcome_yes=False, seed_from_deltas=True)  # smoke-test new mode
    assert s["seq_gaps"] == 1, f"seq-gap detection failed: {s['seq_gaps']}"
    assert s["neg_clamps"] == 1, "negative-clamp detection failed"
    print("\nSELF-TEST PASSED: seq-gap and negative-clamp detection both fired correctly.")
    os.remove(fp)
