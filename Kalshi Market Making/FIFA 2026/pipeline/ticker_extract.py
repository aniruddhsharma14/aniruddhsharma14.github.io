"""
Extract the exchange `ticker` channel -- Kalshi's own published best bid / ask --
for every KXWCGAME market on one tape day.

Why the ticker channel and not the reconstructed books: H11 (hypotheses.md) needs
prices for FUTURE matches on earlier days, which the canonical layer never kept,
and pre-match books rebuilt from deltas would start cold. The ticker channel needs
no reconstruction. Verified against the v2 book: 100.00% identical bid and ask
over 3,834 in-play updates on 26JUN14GERCUW-GER.

SEALED SET. Every fixture in a group that contains a sealed match (splits/v2.csv)
is dropped HERE, at extraction, so sealed prices never reach disk in this panel.
Reading raw tapes bypasses canon_load's guard, so the guard is re-imposed here.
The excluded list is written to canonical/ticker/EXCLUDED.txt.

    python3 pipeline/ticker_extract.py kalshi-2026-06-14.jsonl.gz
Writes canonical/ticker/<YYYY-MM-DD>.parquet
"""
from __future__ import annotations
import os, sys, json, re, subprocess
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')
OUT = os.path.join(CANON, 'ticker')


def groups_of_fixtures():
    """Group = connected component of the full 72-fixture team graph."""
    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    ms = sorted(inv.ticker[inv.ticker.str.startswith('KXWCGAME')]
                .str.split('-').str[1].unique())
    par = {}

    def find(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for m in ms:
        par[find(m[7:10])] = find(m[10:13])
    return {m: find(m[7:10]) for m in ms}


def excluded_fixtures():
    g = groups_of_fixtures()
    sp = pd.read_csv(os.path.join(ROOT, 'splits', 'v2.csv'))
    sealed = set(sp.loc[sp.split == 'sealed', 'match'])
    if len(sealed) != 6:
        sys.exit(f'expected 6 sealed matches in splits/v2.csv, found {len(sealed)}')
    bad_groups = {g[m] for m in sealed}
    return sorted(m for m, gr in g.items() if gr in bad_groups), sorted(sealed)


def stream(path):
    """Yield raw ticker-channel lines for KXWCGAME, prefiltered by grep for speed."""
    cat = ['gzip', '-dc', path] if path.endswith('.gz') else ['cat', path]
    p1 = subprocess.Popen(cat, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(['grep', '-F', 'ticker\\",'], stdin=p1.stdout,
                          stdout=subprocess.PIPE)
    p3 = subprocess.Popen(['grep', '-F', 'KXWCGAME-'], stdin=p2.stdout,
                          stdout=subprocess.PIPE, text=True)
    p1.stdout.close(); p2.stdout.close()
    for line in p3.stdout:
        yield line
    p3.wait()


def f(x):
    return float(x) if x not in (None, '') else float('nan')


def main():
    path = sys.argv[1]
    day = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path)).group(1)
    excl, sealed = excluded_fixtures()
    excl_set = set(excl)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'EXCLUDED.txt'), 'w') as fh:
        fh.write('# fixtures in groups containing a sealed match (splits/v2.csv)\n')
        fh.write('# sealed: ' + ', '.join(sealed) + '\n')
        fh.write('\n'.join(excl) + '\n')

    rows, n_seen, n_drop = [], 0, 0
    for line in stream(path):
        try:
            o = json.loads(line)
            m = json.loads(o['raw'])
        except Exception:
            continue
        if m.get('type') != 'ticker':
            continue
        x = m.get('msg') or {}
        tk = x.get('market_ticker', '')
        if not tk.startswith('KXWCGAME-'):
            continue
        n_seen += 1
        _, match, leg = tk.split('-', 2)
        if match in excl_set:
            n_drop += 1
            continue
        rows.append((int(x['ts_ms']), int(o.get('ts_recv_ms') or 0), match, leg,
                     100 * f(x.get('yes_bid_dollars')), 100 * f(x.get('yes_ask_dollars')),
                     f(x.get('yes_bid_size_fp')), f(x.get('yes_ask_size_fp')),
                     100 * f(x.get('price_dollars')), f(x.get('volume_fp')),
                     f(x.get('open_interest_fp'))))
    d = pd.DataFrame(rows, columns=['ts', 'recv', 'match', 'leg', 'bid', 'ask',
                                    'bid_sz', 'ask_sz', 'last', 'volume', 'oi'])
    d = d.sort_values(['ts', 'match', 'leg']).reset_index(drop=True)
    d.to_parquet(os.path.join(OUT, f'{day}.parquet'), index=False)
    print(f'{day}: {n_seen:,} ticker msgs, {n_drop:,} dropped (sealed groups), '
          f'{len(d):,} kept across {d.match.nunique() if len(d) else 0} matches')


if __name__ == '__main__':
    main()
