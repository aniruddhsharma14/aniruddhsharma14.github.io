"""
Stage 0a2 -- MATCH WINDOWS, found empirically from trade density.

Why not a fixture list: a hand-typed kickoff table is one transcription error away
from silently analysing the wrong 2 hours, and it does not tell us when the market
actually woke up. Trade flow does. A match is the contiguous run of minutes around
the busiest minute of the day for that match's three legs combined.

Threshold is a FRACTION OF PEAK, never an absolute count: a 500k-trade market
clears any absolute floor all day long, which is how an earlier version of this
produced 00:00 -> 00:00 windows.

Byte-level scan, no JSON parsing.
Usage: python3 stage0_windows.py <tape> <out.csv>
"""
import sys, re, gzip, csv, os
from collections import defaultdict

RE_TRADE = re.compile(rb'\\"type\\":\\"trade\\"')
RE_TK    = re.compile(rb'\\"market_ticker\\":\\"(KXWCGAME-([A-Z0-9]+)-[A-Z0-9]+)\\"')
RE_TS    = re.compile(rb'\\"ts_ms\\":(\d+)')


def _open(p):
    return gzip.open(p, 'rb') if str(p).endswith('.gz') else open(p, 'rb')


def scan(path):
    """-> {match: {minute: trade_count}} summed over the match's three legs."""
    hist = defaultdict(lambda: defaultdict(int))
    with _open(path) as f:
        for line in f:
            if b'KXWCGAME' not in line or not RE_TRADE.search(line):
                continue
            mt = RE_TK.search(line); ms = RE_TS.search(line)
            if not mt or not ms:
                continue
            hist[mt.group(2).decode()][int(ms.group(1)) // 60000] += 1
    return hist


def main():
    path, out = sys.argv[1], sys.argv[2]
    hist = scan(path)
    new = not os.path.exists(out)
    with open(out, 'a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['file', 'match', 'minute', 'trades'])
        base = os.path.basename(path)
        for m in sorted(hist):
            for mi in sorted(hist[m]):
                w.writerow([base, m, mi, hist[m][mi]])
    print(f"{os.path.basename(path)}: {len(hist)} matches", flush=True)


if __name__ == '__main__':
    main()
