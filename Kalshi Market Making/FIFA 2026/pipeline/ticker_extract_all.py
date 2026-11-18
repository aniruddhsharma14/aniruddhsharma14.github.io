"""
Extract the exchange `ticker` channel for every World Cup series EXCEPT the match
result series (KXWCGAME, already in canonical/ticker/): group outcomes,
qualification, tournament winner, stage reached, goal ladders, player props.

    python3 pipeline/ticker_extract_all.py kalshi-2026-06-14.jsonl.gz
Writes canonical/ticker_all/<YYYY-MM-DD>.parquet and <YYYY-MM-DD>.contracts.csv

SEALED SET -- two rules, both applied HERE so sealed information never reaches disk:

 (a) Drop any contract that names a sealed-group team or a sealed group: e.g.
     "Netherlands wins the tournament", "Group F winner", Iran props, Belgian
     players' goal counts.
 (b) For every remaining contract, drop rows whose timestamp falls inside the
     in-play window of any fixture in a sealed group (kickoff -> end + 10 min).
     Needed even for other groups' contracts: in the 48-team format the eight best
     third-placed teams qualify, so a Group F result moves Group A's qualification
     prices. Windows come from windows.csv INCLUDING placeholder ones, which errs
     towards dropping more, never less.

Sealed groups are derived from splits/v2.csv, and the group-letter table below is
asserted against the KXWCGAME team graph, so a wrong hardcoded letter fails loudly.
"""
from __future__ import annotations
import os, sys, json, re, subprocess
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')
OUT = os.path.join(CANON, 'ticker_all')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ticker_extract import groups_of_fixtures

PREFIXES = ('KXWC', 'KXMENWORLDCUP', 'KXSOCCERPLAY', 'KXPLAYWC')
SKIP = ('KXWCGAME-',)                       # match results live in canonical/ticker/

# Group letters, as listed by KXWCGROUPWIN-26<letter>-<team> on the June 14 tape.
GROUP_LETTERS = {
    'A': 'CZE KOR MEX RSA', 'B': 'BIH CAN QAT SUI', 'C': 'BRA HTI MAR SCO',
    'D': 'AUS PAR TUR USA', 'E': 'CIV CUW ECU GER', 'F': 'JPN NED SWE TUN',
    'G': 'BEL EGY IRI NZL', 'H': 'CPV ESP KSA URU', 'I': 'FRA IRQ NOR SEN',
    'J': 'ARG AUT DZA JOR', 'K': 'COD COL POR UZB', 'L': 'CRO ENG GHA PAN'}
# KXMENWORLDCUP mixes 2- and 3-letter codes. Only the sealed teams' codes are needed.
MWC_CODE = {'NED': 'NL', 'JPN': 'JP', 'SWE': 'SE', 'TUN': 'TN',
            'BEL': 'BE', 'EGY': 'EGY', 'IRI': 'IR', 'NZL': 'NZL'}
GROUP_SCOPED = {'KXWCGROUPWIN', 'KXWCGROUPQUAL', 'KXWCGROUPBOTTOM'}   # 26<letter>-<team>
PAD_AFTER_MS = 10 * 60_000


def sealed_scope():
    comp = groups_of_fixtures()                       # match -> component id
    letter_of = {t: L for L, ts in GROUP_LETTERS.items() for t in ts.split()}
    # assert the hardcoded letters agree with the fixture graph
    for m, g in comp.items():
        a, b = m[7:10], m[10:13]
        if letter_of[a] != letter_of[b]:
            sys.exit(f'group letter table contradicts fixture {m}: {a}->{letter_of[a]} '
                     f'{b}->{letter_of[b]}')
    sp = pd.read_csv(os.path.join(ROOT, 'splits', 'v2.csv'))
    sealed = set(sp.loc[sp.split == 'sealed', 'match'])
    letters = {letter_of[m[7:10]] for m in sealed}
    teams = {t for L in letters for t in GROUP_LETTERS[L].split()}
    fixtures = sorted(m for m in comp if letter_of[m[7:10]] in letters)
    w = pd.read_csv(os.path.join(CANON, 'windows.csv')).set_index('match')
    wins = [(int(w.loc[m, 'kickoff_ms']), int(w.loc[m, 'end_ms']) + PAD_AFTER_MS)
            for m in fixtures if m in w.index]
    return letters, teams, fixtures, wins


def references_sealed(ticker, letters, teams):
    series, *rest = ticker.split('-')
    toks = [t[2:] if t.startswith('26') else t for t in rest]       # strip '26'
    if series in GROUP_SCOPED and toks and toks[0] in letters:
        return 'sealed group'
    if series == 'KXWCGROUPWINNER' and toks and toks[-1] in letters:
        return 'sealed group'
    if series == 'KXWCGROUPGOALS' and toks and toks[-1] in letters:
        return 'sealed group'
    if series == 'KXWCIRAN':
        return 'sealed team (Iran)'
    if series == 'KXMENWORLDCUP' and toks and toks[-1] in {MWC_CODE[t] for t in teams}:
        return 'sealed team'
    if any(t in teams for t in toks):                 # TEAMGOALS, PLAYERGOALS, FURTHEST...
        return 'sealed team'
    return ''


def stream(path):
    cat = ['gzip', '-dc', path] if path.endswith('.gz') else ['cat', path]
    p1 = subprocess.Popen(cat, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(['grep', '-F', 'ticker\\",'], stdin=p1.stdout, stdout=subprocess.PIPE)
    p3 = subprocess.Popen(['grep', '-vF', 'KXWCGAME-'], stdin=p2.stdout,
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
    letters, teams, fixtures, wins = sealed_scope()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'EXCLUDED.txt'), 'w') as fh:
        fh.write(f'# sealed groups: {sorted(letters)}  teams: {sorted(teams)}\n')
        fh.write('# rule (a): contracts naming these teams or groups are dropped entirely\n')
        fh.write('# rule (b): every other contract loses rows inside these fixtures\' windows\n')
        fh.write('\n'.join(fixtures) + '\n')
    win_s = sorted(wins)

    def in_sealed_window(ts):
        return any(a <= ts <= b for a, b in win_s)

    rows, cat = [], {}
    n_seen = n_a = n_b = 0
    for line in stream(path):
        try:
            o = json.loads(line); m = json.loads(o['raw'])
        except Exception:
            continue
        if m.get('type') != 'ticker':
            continue
        x = m.get('msg') or {}
        tk = x.get('market_ticker', '')
        if not tk.startswith(PREFIXES) or tk.startswith(SKIP):
            continue
        n_seen += 1
        series = tk.split('-')[0]
        why = cat.get(tk)
        if why is None:
            why = cat[tk] = references_sealed(tk, letters, teams)
        if why:
            n_a += 1; continue
        ts = int(x['ts_ms'])
        if in_sealed_window(ts):
            n_b += 1; continue
        rows.append((ts, series, tk, 100 * f(x.get('yes_bid_dollars')),
                     100 * f(x.get('yes_ask_dollars')), f(x.get('yes_bid_size_fp')),
                     f(x.get('yes_ask_size_fp')), 100 * f(x.get('price_dollars')),
                     f(x.get('volume_fp')), f(x.get('open_interest_fp'))))
    d = pd.DataFrame(rows, columns=['ts', 'series', 'ticker', 'bid', 'ask', 'bid_sz',
                                    'ask_sz', 'last', 'volume', 'oi'])
    d = d.sort_values(['ts', 'ticker']).reset_index(drop=True)
    d.to_parquet(os.path.join(OUT, f'{day}.parquet'), index=False)
    pd.DataFrame([dict(ticker=t, series=t.split('-')[0], excluded=r) for t, r in sorted(cat.items())]
                 ).to_csv(os.path.join(OUT, f'{day}.contracts.csv'), index=False)
    print(f'{day}: {n_seen:,} msgs | dropped rule(a) {n_a:,}, rule(b) {n_b:,} | kept '
          f'{len(d):,} rows, {d.ticker.nunique() if len(d) else 0} contracts, '
          f'{d.series.nunique() if len(d) else 0} series')


if __name__ == '__main__':
    main()
