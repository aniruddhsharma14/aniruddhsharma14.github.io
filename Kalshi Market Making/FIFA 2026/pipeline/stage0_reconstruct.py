"""
Stage 0b -- RECONSTRUCT one UTC day file into canonical Parquet.

Writes TWO layers per leg-day. The split matters:

  raw/   the actual source events -- every orderbook delta and every snapshot
         level, lossless, in Kalshi's own string fields. Nothing is derived,
         nothing is truncated. Any future view can be rebuilt from this without
         touching the 16 GB of gzip again.

  books/ a reconstructed state series, carrying the FULL ladder at every
         top-of-book change (median ~69 bid levels, not a fixed 5).

An earlier version stored only a depth-5 ladder and no raw layer. That was wrong:
it threw away the deep book the tapes already contained, and the only way to get
it back was to re-reconstruct everything. Depth is a view, not a storage decision.

MEMORY: a full ladder holds ~280 Python objects per row, against ~20 for a
depth-5 one. Buffering a whole day for 21 legs OOM-killed the process. Rows are
now flushed to numbered part files every CHUNK rows, so memory stays flat
regardless of how busy the day is. The loader globs parts, so this is invisible
downstream.

--------------------------------------------------------------------------------
Day files MUST run in chronological order: each leg's book is carried across the
UTC boundary in a pickle, because an evening-ET kickoff lands in the NEXT UTC file
(KOR-CZE kicks off 02:00 UTC on June 12, built from the June 11 file).

Warm-up: a leg activates at the start of the UTC day BEFORE its window when that
window opens in the first 6 hours of a day, so a delta-built book gets hours of
lead-in. Any populated snapshot on the way resets the book exactly.

Crash safety: book state is committed only after every Parquet part for the day is
on disk. Stale parts from an interrupted run are deleted before writing, so a
rerun cannot leave a mix of old and new.

Usage: python3 stage0_reconstruct.py <tapefile>
"""
from __future__ import annotations
import sys, os, glob, pickle
from datetime import datetime, timezone
import pandas as pd
import pyarrow as pa, pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon import DualBook, iter_tape

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')

# OUT_SUFFIX writes to a PARALLEL tree (books_v2, raw_v2, ...) so a rebuild can be
# compared against the existing layer before anything replaces it, and abandoned by
# deleting one directory. Default '' is the live tree.
SUF   = os.environ.get('OUT_SUFFIX', '')
BOOKS = os.path.join(CANON, 'books' + SUF)
RAW   = os.path.join(CANON, 'raw' + SUF)
STATE = os.path.join(CANON, '_state' + SUF)
MANI  = os.path.join(CANON, '_manifest' + SUF)
DAY_MS, EARLY_H = 86400000, 6

# ---- knobs ----------------------------------------------------------------
LADDER_DEPTH = None      # None = full book. Set an int only if disk forces it.
EMIT_ON = 'touch'        # 'touch' = on top-of-book change; 'any' = every delta.
RAW_FROM_ACTIVATION = True   # keep warm-up events: they are what seed the book
CHUNK      = 40_000      # book rows per part file
CHUNK_RAW  = 400_000     # raw rows per part file

# SEED_EXTEND activates a leg from the UTC day of the LAST POPULATED SNAPSHOT
# before its window, instead of one day of lead-in. Without it, a snapshot sitting
# in the June 13 tape is never read when rebuilding a June 25 match, so the book
# starts EMPTY and every order resting beforehand is invisible. Measured cost of
# not doing this: median 82 of 132 price levels wrong, 2.9M contracts of missing
# size on levels that held the touch (canonical/size_deficit_classified.csv).
#
# It applies ONLY where seed_feasibility.csv shows an unbroken recording path from
# that snapshot to the window. Seeding across the June 17-21 gap would replay
# post-gap deltas onto a stale state, making sizes wrong in BOTH directions; an
# empty start is worse but stays a one-directional lower bound, which is the
# property that keeps the data usable. Those legs are deliberately left alone.
SEED_EXTEND = os.environ.get('SEED_EXTEND', '') == '1'
ONLY = [m for m in os.environ.get('ONLY_MATCHES', '').split(',') if m]


def utc_day_start(ms):
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class PartWriter:
    """Buffers rows and flushes numbered Parquet parts, so memory stays flat."""

    def __init__(self, directory, stem, chunk, columns=None):
        self.dir, self.stem, self.chunk, self.columns = directory, stem, chunk, columns
        self.buf, self.part, self.total = [], 0, 0

    def add(self, row):
        self.buf.append(row)
        if len(self.buf) >= self.chunk:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        os.makedirs(self.dir, exist_ok=True)
        df = (pd.DataFrame(self.buf, columns=self.columns) if self.columns
              else pd.DataFrame(self.buf))
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                       os.path.join(self.dir, f'{self.stem}.p{self.part:03d}.parquet'),
                       compression='zstd')
        self.total += len(self.buf)
        self.part += 1
        self.buf = []


def main():
    path = sys.argv[1]
    for d in (BOOKS, RAW, STATE):
        os.makedirs(d, exist_ok=True)
    base = os.path.basename(path)
    day = base.split('kalshi-')[1][:10]
    day0 = int(datetime.strptime(day, '%Y-%m-%d')
               .replace(tzinfo=timezone.utc).timestamp() * 1000)

    w = pd.read_csv(os.path.join(CANON, 'windows.csv'))
    w = w[w.covered]
    if ONLY:
        w = w[w.match.isin(ONLY)]

    # per-match activation override: the day of the last populated snapshot, but
    # only where the recording path from there to the window has no missing days
    seed_day = {}
    if SEED_EXTEND:
        fp = os.path.join(CANON, 'seed_feasibility.csv')
        sf = pd.read_csv(fp)
        sf = sf[sf.last_pop_snap.notna() & (sf.missing_days.fillna('') == '')]
        for m, g in sf.groupby('match'):
            # earliest seed across the match's legs, so all three share one start
            d = min(g.last_pop_snap)
            seed_day[m] = int(datetime.strptime(d, '%Y-%m-%d')
                              .replace(tzinfo=timezone.utc).timestamp() * 1000)

    act, wins = {}, {}
    for r in w.itertuples():
        h = (r.t0_ms - utc_day_start(r.t0_ms)) / 3600000.0
        a = utc_day_start(r.t0_ms) - (DAY_MS if h < EARLY_H else 0)
        if r.match in seed_day:
            a = min(a, seed_day[r.match])      # never activate LATER than before
        if a <= day0 and r.t1_ms >= day0:
            act[r.match] = a
            wins[r.match] = (int(r.t0_ms), int(r.t1_ms))
    # A very busy day can exceed a single remote call's time limit. Each leg's book
    # is independent, so the day can be processed in slices across several calls:
    # the state pickle is loaded at start and saved at end, so slices compose.
    #   MATCH_OFFSET / MATCH_LIMIT select a contiguous slice of this day's matches.
    off = int(os.environ.get('MATCH_OFFSET', 0))
    lim = os.environ.get('MATCH_LIMIT')
    if lim:
        keep = sorted(act)[off:off + int(lim)]
        act = {m: act[m] for m in keep}
        wins = {m: wins[m] for m in keep}
        print(f"  [slice {off}..{off+int(lim)}] {len(act)} matches: {', '.join(keep)}", flush=True)

    if not act:
        print(f"{base}: no covered match needs this day"); return

    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    inv['m'] = inv.ticker.str.extract(r'KXWCGAME-(\w+?)-')[0]
    legs = {m: sorted(inv[inv.m == m].ticker.unique()) for m in act}
    tick2match = {t: m for m, ts in legs.items() for t in ts}
    tickers = set(tick2match)

    books, prov = {}, {}
    sf = os.path.join(STATE, 'books.pkl')
    if os.path.exists(sf):
        books, prov = pickle.load(open(sf, 'rb'))

    bw, rw, tw = {}, {}, {}
    for t in tickers:
        books.setdefault(t, DualBook())
        books[t].has_book = True
        prov.setdefault(t, dict(pop_snaps_before_t0=0, last_pop_snap_ms=None,
                                first_event_ms=None, deltas_before_t0=0))
        m = tick2match[t]
        # drop stale parts from an interrupted earlier run for this leg-day
        for p in glob.glob(os.path.join(BOOKS, m, f'{t}__{day}.*')) + \
                 glob.glob(os.path.join(RAW, m, f'{t}__{day}.*')):
            os.remove(p)
        bw[t] = PartWriter(os.path.join(BOOKS, m), f'{t}__{day}.book', CHUNK)
        tw[t] = PartWriter(os.path.join(BOOKS, m), f'{t}__{day}.trades', CHUNK,
                           columns=['ts', 'price', 'size', 'aggressor'])
        rw[t] = PartWriter(os.path.join(RAW, m), f'{t}__{day}.raw', CHUNK_RAW,
                           columns=['ts', 'sequence', 'kind', 'side',
                                    'price_dollars', 'amount_fp'])

    last_top = {t: None for t in tickers}

    for typ, tk, msg, seq, ts in iter_tape(path, tickers):
        if ts is None:
            continue
        bk, pv = books[tk], prov[tk]
        t0, t1 = wins[tick2match[tk]]
        if ts > t1:
            continue
        if pv['first_event_ms'] is None:
            pv['first_event_ms'] = ts
        keep_raw = RAW_FROM_ACTIVATION or ts >= t0

        if typ == 'orderbook_snapshot':
            if keep_raw:
                for side, key in (('yes', 'yes_dollars_fp'), ('no', 'no_dollars_fp')):
                    for p, s in (msg.get(key) or []):
                        rw[tk].add((ts, seq, 'snap', side, str(p), str(s)))
            if bk.snapshot(msg) and ts < t0:
                pv['pop_snaps_before_t0'] += 1
                pv['last_pop_snap_ms'] = ts

        elif typ == 'orderbook_delta':
            # price_dollars / delta_fp are Kalshi's lossless string fields.
            # Stored verbatim -- never a rounded int copy.
            if keep_raw:
                rw[tk].add((ts, seq, 'delta', msg['side'],
                            str(msg['price_dollars']), str(msg['delta_fp'])))
            bk.delta(msg, seq)
            if ts < t0:
                pv['deltas_before_t0'] += 1

        elif typ == 'trade':
            if ts >= t0:
                # Kalshi docs: book_side 'bid' == outcome 'yes'. The field names the
                # outcome the TAKER BOUGHT. bought YES -> aggressive buy of YES;
                # bought NO -> economically an aggressive sell of YES.
                agg = 'buy' if msg.get('taker_book_side') == 'bid' else 'sell'
                tw[tk].add((ts, int(round(float(msg['yes_price_dollars']) * 100)),
                            float(msg.get('count_fp', 0) or 0), agg))
            continue

        if ts < t0:
            continue
        b, a = bk.best_bid(), bk.best_ask()
        if b is None or a is None:
            continue
        # Decide whether to emit BEFORE building the ladder. ladder() sorts the whole
        # book, and at full depth that is the dominant cost; doing it on every event
        # rather than every emitted row made a busy day exceed any sane time budget.
        # Touch sizes come straight from the dicts, no sort needed.
        bv, av = bk.bids.get(b, 0.0), bk.asks.get(a, 0.0)
        top = (b, a, bv, av)
        if EMIT_ON == 'touch' and top == last_top[tk]:
            continue
        last_top[tk] = top
        bp, bs, ap, as_ = bk.ladder(LADDER_DEPTH)
        if not bp or not ap:
            continue
        micro = (b * av + a * bv) / (bv + av) if (bv + av) > 0 else (b + a) / 2.0
        bw[tk].add(dict(
            ts=ts, bid=b, ask=a, bid_size=bv, ask_size=av,
            mid=(b + a) / 2.0, microprice=micro, spread=a - b,
            n_bid_levels=len(bp), n_ask_levels=len(ap),
            bid_depth=float(sum(bs)), ask_depth=float(sum(as_)),
            bid_prices=[int(x) for x in bp], bid_sizes=[float(x) for x in bs],
            ask_prices=[int(x) for x in ap], ask_sizes=[float(x) for x in as_]))

    for d in (bw, rw, tw):
        for t in d:
            d[t].flush()

    manifest = []
    for tk in sorted(tickers):
        pv = prov[tk]
        manifest.append(dict(file=base, match=tick2match[tk], ticker=tk,
                             book_rows=bw[tk].total, trade_rows=tw[tk].total,
                             raw_rows=rw[tk].total,
                             seq_gaps=books[tk].seq_gaps, neg_clamps=books[tk].neg_clamps,
                             n_snapshots_applied=books[tk].n_snapshots,
                             uncross_prunes=books[tk].uncross_prunes,
                             pop_snaps_before_t0=pv['pop_snaps_before_t0'],
                             last_pop_snap_ms=pv['last_pop_snap_ms'],
                             deltas_before_t0=pv['deltas_before_t0'],
                             t0_ms=wins[tick2match[tk]][0], t1_ms=wins[tick2match[tk]][1]))

    md = MANI; os.makedirs(md, exist_ok=True)
    # a sliced day writes one manifest per slice, or the second slice would
    # overwrite the first and the row counts would silently halve
    tag = base + (f'.off{off}.csv' if lim else '.csv')
    pd.DataFrame(manifest).to_csv(os.path.join(md, tag), index=False)

    tmp = sf + '.tmp'
    with open(tmp, 'wb') as fh:
        pickle.dump((books, prov), fh); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, sf)

    tb = sum(x['book_rows'] for x in manifest)
    tt = sum(x['trade_rows'] for x in manifest)
    tr = sum(x['raw_rows'] for x in manifest)
    print(f"{base}: {len(act)} matches, {len(tickers)} legs -> "
          f"{tb:,} book, {tr:,} raw, {tt:,} trades", flush=True)


if __name__ == '__main__':
    main()
