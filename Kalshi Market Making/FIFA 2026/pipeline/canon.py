"""
Stage 0b -- CANONICAL RECONSTRUCTION.

One code path for order-book reconstruction, used by every downstream experiment.
Nothing else in this project is allowed to open a raw tape again.

--------------------------------------------------------------------------------
Kalshi specifics, all handled here so no caller has to remember them:

 * YES and NO are SEPARATE books. A resting NO bid at price q is economically a
   YES offer at (100 - q). So:
       best_ask(YES) = 100 - max(NO bids)
   Nearly every reconstruction bug in this project traced back to this one line.

 * `delta_fp` is a SIGNED change to a price level's resting size, not a new size.

 * `seq` is a GLOBAL counter shared by every channel and market on the socket, so
   this market's deltas are a forward-skipping subsequence (3, 6, 9, ...). A real
   gap is seq <= last_seq (backward/repeat), NOT a forward jump.

 * taker_book_side: per Kalshi docs, book_side 'bid' == outcome 'yes'. The field
   names the outcome the taker BOUGHT, not the resting side that was hit. So
   'bid' -> aggressive BUY of YES, 'ask' -> aggressive SELL of YES.
   (An earlier version of this project had this inverted, which silently
   attributed every fill to the wrong side. Do not "simplify" it.)

 * Sizes net to float residue (+98873.6 - 98873.6 != 0 exactly), so a level below
   half a contract is treated as empty. Leftover dust poisons best_bid/best_ask.
--------------------------------------------------------------------------------
"""
from __future__ import annotations
import json, gzip, re, os
from collections import defaultdict

DUST = 0.5          # a level holding less than half a contract is empty
DEPTH = 5           # ladder depth stored in the canonical rows


def open_tape(path):
    """Transparently read .gz or plain .jsonl."""
    return gzip.open(path, 'rb') if str(path).endswith('.gz') else open(path, 'rb')


def cents(price_dollars) -> int:
    """'0.4300' -> 43. Kalshi ticks are integer cents; verified 0 non-integer
    prices across 3,000,001 observed trades."""
    return int(round(float(price_dollars) * 100))


# ------------------------------------------------------------------ the book
class DualBook:
    """Two ladders in YES terms: bids from the yes-book, asks from the inverted no-book."""

    def __init__(self):
        self.bids: dict[int, float] = {}
        self.asks: dict[int, float] = {}
        self.last_seq = None
        self.seq_gaps = 0
        self.neg_clamps = 0
        self.has_book = False
        self.n_snapshots = 0
        self.uncross_prunes = 0
        # Cached touch. best_bid/best_ask scanned the whole book on every event,
        # which at full depth dominated the run: a busy day could not finish.
        # Most deltas do not move the touch, so the scan is only redone when a
        # delta actually invalidates it.
        self._bb = None; self._ba = None
        self._bb_dirty = True; self._ba_dirty = True

    def __setstate__(self, state):
        # books pickled before the cache existed must not inherit a stale touch
        self.__dict__.update(state)
        self._bb = None; self._ba = None
        self._bb_dirty = True; self._ba_dirty = True

    def snapshot(self, msg):
        yes = msg.get('yes_dollars_fp') or []
        no = msg.get('no_dollars_fp') or []
        if not yes and not no:
            return False            # empty snapshot = heartbeat/ack, NOT a reset
        self.bids.clear(); self.asks.clear()
        for p, s in yes:
            self.bids[cents(p)] = float(s)          # resting YES buys  = bids
        for p, s in no:
            self.asks[100 - cents(p)] = float(s)    # resting NO buys   = YES asks
        self.last_seq = None
        self.has_book = True
        self.n_snapshots += 1
        self._bb_dirty = self._ba_dirty = True
        return True

    def _uncross(self, just: str):
        """Drop stale levels after a crossed book.

        A resting bid at or above the best ask cannot exist -- it would have traded.
        So a crossed book always means one side is stale, and the side that just
        received an update is the authoritative one.

        This is not cosmetic. A dropped-message burst leaves one side FROZEN: its
        levels never receive another delta, so they persist for the rest of the
        match. 26JUN12CANBIH-CAN (135 seq gaps, the worst in the dataset) sat with
        bid=53, bid_size=6982.53 unchanged while its ask walked 54 -> 19, leaving
        85% of states crossed and a median spread of -23 ticks. Without this rule
        that match is unusable; the same failure was quietly present at the 1-3%
        level in six others.
        """
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None or bb < ba:
            return
        if just == 'bid':
            stale = [p for p in self.asks if p <= bb]
            for p in stale:
                self.asks.pop(p, None)
            if stale:
                self._ba_dirty = True
        else:
            stale = [p for p in self.bids if p >= ba]
            for p in stale:
                self.bids.pop(p, None)
            if stale:
                self._bb_dirty = True
        self.uncross_prunes += len(stale)

    def delta(self, msg, seq):
        if self.last_seq is not None and seq is not None and seq <= self.last_seq:
            self.seq_gaps += 1
        if seq is not None:
            self.last_seq = seq
        pc = cents(msg['price_dollars']); d = float(msg['delta_fp'])
        is_bid = msg['side'] == 'yes'
        book, key = (self.bids, pc) if is_bid else (self.asks, 100 - pc)
        new = book.get(key, 0.0) + d
        if new < 0:
            self.neg_clamps += 1
            new = 0.0
        if new < DUST:
            book.pop(key, None)
            # only losing the current best invalidates the cache
            if is_bid:
                if key == self._bb: self._bb_dirty = True
            elif key == self._ba:
                self._ba_dirty = True
        else:
            book[key] = new
            if is_bid:
                if not self._bb_dirty and (self._bb is None or key > self._bb):
                    self._bb = key
            elif not self._ba_dirty and (self._ba is None or key < self._ba):
                self._ba = key
        self._uncross('bid' if is_bid else 'ask')

    # --- top of book -------------------------------------------------------
    def best_bid(self):
        if self._bb_dirty:
            live = [p for p, s in self.bids.items() if s >= DUST]
            self._bb = max(live) if live else None
            self._bb_dirty = False
        return self._bb

    def best_ask(self):
        """Lowest price anyone will sell YES at = 100 - (highest NO bid)."""
        if self._ba_dirty:
            live = [p for p, s in self.asks.items() if s >= DUST]
            self._ba = min(live) if live else None
            self._ba_dirty = False
        return self._ba

    def ladder(self, n=None):
        """-> (bid_prices, bid_sizes, ask_prices, ask_sizes).

        n=None returns the FULL book, every live level, variable length.
        Truncating here was a mistake: it silently threw away the deep book that
        the tapes already contain, and no downstream code could get it back
        without a full re-reconstruction. Depth is now a view, not a storage
        decision. Pass n only when a caller genuinely wants the top n.
        """
        bp = sorted((p for p, s in self.bids.items() if s >= DUST), reverse=True)
        ap = sorted(p for p, s in self.asks.items() if s >= DUST)
        if n is not None:
            bp, ap = bp[:n], ap[:n]
        return bp, [self.bids[p] for p in bp], ap, [self.asks[p] for p in ap]

    def mid(self):
        b, a = self.best_bid(), self.best_ask()
        return (b + a) / 2.0 if (b is not None and a is not None) else None

    def microprice(self, levels=1):
        """Volume-weighted fair value, CROSS-multiplied: heavy bid volume pulls the
        price toward the ask. Always strictly inside the spread.
            micro = (bid*ask_vol + ask*bid_vol) / (bid_vol + ask_vol)
        levels=1 uses the touch only; deeper levels drag in the 1c/99c walls."""
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        bp, bs, ap, as_ = self.ladder(levels)
        bv = sum(bs); av = sum(as_)
        if bv + av <= 0:
            return (b + a) / 2.0
        return (b * av + a * bv) / (bv + av)


# ------------------------------------------------------- tape iteration
RE_HAS = re.compile(rb'KXWCGAME')


def iter_tape(path, tickers: set[str]):
    """Yield (typ, msg, seq, ts_ms) for the given tickers, in file order.

    Two cheap rejects before any JSON parsing: the line must contain KXWCGAME at
    all, and then one of the wanted ticker strings. Only survivors are parsed.
    Over ~58 GB of tape this is the difference between minutes and hours."""
    pats = [t.encode() for t in tickers]
    with open_tape(path) as f:
        for line in f:
            if not RE_HAS.search(line):
                continue
            if not any(p in line for p in pats):
                continue
            try:
                env = json.loads(line)
                raw = json.loads(env['raw'])
            except Exception:
                continue
            typ = raw.get('type')
            if typ not in ('orderbook_snapshot', 'orderbook_delta', 'trade'):
                continue
            msg = raw.get('msg', {})
            tk = msg.get('market_ticker')
            if tk not in tickers:
                continue
            # exchange time preferred; receive time only as fallback
            ts = msg.get('ts_ms') or env.get('ts_recv_ms')
            yield typ, tk, msg, raw.get('seq'), ts


def trade_minutes(path, tickers: set[str]):
    """Pass A: cheap per-minute trade histogram per ticker, used to locate the
    match window empirically rather than trusting a hand-typed fixture list."""
    RE_T = re.compile(rb'\\"type\\":\\"trade\\"')
    RE_TK = re.compile(rb'\\"market_ticker\\":\\"(KXWCGAME-[A-Z0-9]+-[A-Z0-9]+)\\"')
    RE_TS = re.compile(rb'\\"ts_ms\\":(\d+)')
    pats = [t.encode() for t in tickers]
    hist = defaultdict(lambda: defaultdict(int))
    with open_tape(path) as f:
        for line in f:
            if not RE_T.search(line):
                continue
            if not any(p in line for p in pats):
                continue
            mt = RE_TK.search(line)
            ms = RE_TS.search(line)
            if not mt or not ms:
                continue
            tk = mt.group(1).decode()
            if tk in tickers:
                hist[tk][int(ms.group(1)) // 60000] += 1
    return hist


def active_window(minute_hist, frac_of_peak=0.25, pad_before_s=1800, pad_after_s=900):
    """Contiguous run of minutes around the busiest minute whose trade count stays
    above frac_of_peak * peak. A fixed count threshold fails badly here: a 230k-trade
    market clears any absolute floor all day, which previously produced 00:00->00:00
    windows. Returns (t0_ms, t1_ms) or None."""
    if not minute_hist:
        return None
    peak_min = max(minute_hist, key=lambda m: minute_hist[m])
    thr = max(1.0, frac_of_peak * minute_hist[peak_min])
    lo = hi = peak_min
    while minute_hist.get(lo - 1, 0) >= thr:
        lo -= 1
    while minute_hist.get(hi + 1, 0) >= thr:
        hi += 1
    return (lo * 60000 - pad_before_s * 1000, (hi + 1) * 60000 + pad_after_s * 1000)
