"""
Stage 0a -- INVENTORY. One cheap pass over every tape file.

Purpose: before reconstructing anything, know what is actually in the data.
For every KXWCGAME-* leg appearing in a file, record:
    event counts by type, first/last exchange timestamp, and whether a
    POPULATED orderbook_snapshot exists (and when) -- that is what decides
    seed_from_deltas later, instead of guessing per match.

Speed: ~58 GB of uncompressed JSON lines total, so we never json.loads a line
we don't need. We test for the literal b'KXWCGAME' in the raw bytes first
(cheap memchr-style scan in C) and only then run precompiled regexes to pull
the four fields we want. No JSON parsing at all in this stage.

Usage:  python3 stage0_inventory.py <tapefile> <out.csv>
"""
import sys, re, gzip, csv, os
from collections import defaultdict

# The recorder stores the Kalshi message as a JSON *string* in "raw", so inside
# the file the inner quotes are backslash-escaped:  \"type\":\"orderbook_delta\"
RE_TICKER = re.compile(rb'\\"market_ticker\\":\\"(KXWCGAME-[A-Z0-9]+-[A-Z0-9]+)\\"')
RE_TYPE   = re.compile(rb'\\"type\\":\\"(\w+)\\"')
RE_TSMS   = re.compile(rb'\\"ts_ms\\":(\d+)')
RE_RECV   = re.compile(rb'"ts_recv_ms":(\d+)')
# a populated snapshot has at least one [price, size] pair in either book
RE_POP    = re.compile(rb'dollars_fp\\":\[\[')


def _open(path):
    return gzip.open(path, 'rb') if str(path).endswith('.gz') else open(path, 'rb')


def scan(path):
    """-> {ticker: stats dict}"""
    st = defaultdict(lambda: dict(
        n_delta=0, n_snapshot=0, n_snapshot_pop=0, n_trade=0, n_other=0,
        ts_first=None, ts_last=None,
        snap_pop_first=None, snap_pop_last=None))
    with _open(path) as f:
        for line in f:
            if b'KXWCGAME' not in line:          # cheap C-level reject
                continue
            mt = RE_TICKER.search(line)
            if not mt:
                continue
            tk = mt.group(1).decode()
            s = st[tk]
            mty = RE_TYPE.search(line)
            typ = mty.group(1).decode() if mty else '?'

            m = RE_TSMS.search(line) or RE_RECV.search(line)
            ts = int(m.group(1)) if m else None
            if ts is not None:
                if s['ts_first'] is None or ts < s['ts_first']:
                    s['ts_first'] = ts
                if s['ts_last'] is None or ts > s['ts_last']:
                    s['ts_last'] = ts

            if typ == 'orderbook_delta':
                s['n_delta'] += 1
            elif typ == 'orderbook_snapshot':
                s['n_snapshot'] += 1
                if RE_POP.search(line):          # non-empty book -> a real seed
                    s['n_snapshot_pop'] += 1
                    if ts is not None:
                        if s['snap_pop_first'] is None or ts < s['snap_pop_first']:
                            s['snap_pop_first'] = ts
                        if s['snap_pop_last'] is None or ts > s['snap_pop_last']:
                            s['snap_pop_last'] = ts
            elif typ == 'trade':
                s['n_trade'] += 1
            else:
                s['n_other'] += 1
    return st


COLS = ['file', 'ticker', 'n_delta', 'n_snapshot', 'n_snapshot_pop', 'n_trade',
        'n_other', 'ts_first', 'ts_last', 'snap_pop_first', 'snap_pop_last']


def main():
    path, out = sys.argv[1], sys.argv[2]
    st = scan(path)
    base = os.path.basename(path)
    new = not os.path.exists(out)
    with open(out, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for tk in sorted(st):
            w.writerow(dict(file=base, ticker=tk, **st[tk]))
    print(f"{base}: {len(st)} legs", flush=True)


if __name__ == '__main__':
    main()
