"""
Stage 1 -- LOCKED SPLITS.

Writes splits/v<N>.csv. Touches nothing else: canonical/ is read-only here.

--------------------------------------------------------------------------------
WHY SPLIT BY COMPONENT, NOT BY MATCH

The working hypothesis that matches of the same team resemble each other is exactly
what breaks a per-match split: with BRA-MAR in explore and another BRA match in test,
a team effect reads as generalisation.

But teams are not independent units either. In the group stage every team plays the
others in its group, so a match is an EDGE between two teams and the teams form
clusters. Splitting two teams of the same group into different sets would leak their
shared match across the boundary. The indivisible unit is therefore the connected
component of the team-vs-team graph -- which comes out as the tournament groups:
11 components of 4 teams, plus 2 two-team fragments whose group-mates' other matches
fell in the recording gaps.

--------------------------------------------------------------------------------
WHY SOME COMPONENTS ARE FORCED INTO EXPLORE

A split file is editable, but one direction of edit is void. Moving a match
sealed -> explore is always safe. Moving explore -> sealed is NOT: once a match has
been mined -- fitted on, used to tune a threshold, or merely eyeballed before deciding
what to try next -- that knowledge sits in every later modelling choice. Relabelling
it "sealed" produces a contaminated test that looks clean, which is worse than having
no test at all.

CONTAMINATED lists matches examined in detail before this split existed. Their whole
components are pinned to explore. This is not a judgement about those matches; it is
bookkeeping about what has already been seen.

--------------------------------------------------------------------------------
HOW THE REMAINING COMPONENTS ARE ASSIGNED

Not at random, and not by eye. Among every assignment of the free components that
hits the requested match counts exactly, we take the one minimising the spread of
mean book-rows-per-match across the three sets. That balances DATA VOLUME, which is
a property of the recording, and is deliberately blind to anything predictive --
so it cannot become a way of choosing a flattering test set. The rule is fixed
before the search runs, and the search is deterministic.
"""
from __future__ import annotations
import sys, os, itertools, json
from datetime import datetime, timezone
from collections import defaultdict
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import matches, teams, validation, ROOT

SPLITS = os.path.join(ROOT, 'splits')
TARGET = dict(explore=24, validation=10, sealed=6)

# Matches examined in detail BEFORE this split existed. See the docstring.
CONTAMINATED = {
    '26JUN11KORCZE': 'reconstruction validation + drift/MM experiments, earlier sessions',
    '26JUN11MEXRSA': 'microprice MM sweeps + leg-sum validation, earlier sessions',
    '26JUN13BRAMAR': 'reconstruction cross-checked against Kalshi published chart',
    '26JUN22ARGAUT': 'ladder / depth-map exploration, earlier sessions',
    '26JUN24MARHTI': 'apparent-arbitrage investigation, Stage 0',
    '26JUN22JORDZA': 'apparent-arbitrage investigation, Stage 0',
    '26JUN27COLPOR': 'deep dive on crossed/inconsistent states, Stage 0',
    '26JUN27CROGHA': 'apparent-arbitrage investigation, Stage 0',
    '26JUN27CODUZB': 'apparent-arbitrage investigation, Stage 0',
    '26JUN12CANBIH': 'frozen-book diagnosis and uncross-rule development, Stage 0',
}


def components(ms):
    adj = defaultdict(set); tmm = defaultdict(set)
    for m in ms:
        a, b = teams(m)
        adj[a].add(b); adj[b].add(a)
        tmm[a].add(m); tmm[b].add(m)
    seen, comps = set(), []
    for t in sorted(adj):
        if t in seen:
            continue
        stack, c = [t], set()
        while stack:
            x = stack.pop()
            if x in c:
                continue
            c.add(x); seen.add(x)
            stack += [y for y in adj[x] if y not in c]
        comps.append(dict(teams=sorted(c),
                          matches=sorted({m for t2 in c for m in tmm[t2]})))
    return sorted(comps, key=lambda c: (-len(c['matches']), c['teams'][0]))


def main():
    ms = matches('AB')
    v = validation()
    comps = components(ms)
    for i, c in enumerate(comps):
        c['id'] = i
        c['n'] = len(c['matches'])
        c['rows'] = int(v.loc[c['matches'], 'book_rows'].sum())
        c['contaminated'] = sorted(set(c['matches']) & set(CONTAMINATED))

    forced = [c for c in comps if c['contaminated']]
    free = [c for c in comps if not c['contaminated']]
    n_forced = sum(c['n'] for c in forced)
    need_explore = TARGET['explore'] - n_forced
    print(f"{len(ms)} matches, {len(comps)} components "
          f"({len(forced)} pinned to explore = {n_forced} matches)")
    if need_explore < 0:
        raise SystemExit(f"contaminated matches ({n_forced}) exceed the explore target")

    # exhaustive, deterministic search over assignments of the free components
    best = None
    idx = list(range(len(free)))
    for r_seal in range(1, len(free)):
        for seal in itertools.combinations(idx, r_seal):
            if sum(free[i]['n'] for i in seal) != TARGET['sealed']:
                continue
            rest = [i for i in idx if i not in seal]
            for r_val in range(1, len(rest) + 1):
                for val in itertools.combinations(rest, r_val):
                    if sum(free[i]['n'] for i in val) != TARGET['validation']:
                        continue
                    exp = [i for i in rest if i not in val]
                    if sum(free[i]['n'] for i in exp) != need_explore:
                        continue
                    dens = []
                    for grp, extra in ((exp, forced), (val, []), (seal, [])):
                        n = sum(free[i]['n'] for i in grp) + sum(c['n'] for c in extra)
                        rw = sum(free[i]['rows'] for i in grp) + sum(c['rows'] for c in extra)
                        dens.append(rw / n)
                    score = max(dens) - min(dens)          # stated rule: balance density
                    key = (score, tuple(sorted(seal)), tuple(sorted(val)))
                    if best is None or key < best[0]:
                        best = (key, exp, list(val), list(seal))
    if best is None:
        raise SystemExit("no assignment hits the requested counts exactly")
    _, exp, val, seal = best

    assign = {}
    for c in forced:
        for m in c['matches']:
            assign[m] = ('explore', c['id'], 'pinned: already examined')
    for grp, lab in ((exp, 'explore'), (val, 'validation'), (seal, 'sealed')):
        for i in grp:
            for m in free[i]['matches']:
                assign[m] = (lab, free[i]['id'], 'balanced assignment')

    rows = []
    for m in ms:
        lab, cid, why = assign[m]
        a, b = teams(m)
        rows.append(dict(match=m, split=lab, component=cid,
                         teams=f'{a}-{b}', tier=v.loc[m, 'tier'],
                         book_rows=int(v.loc[m, 'book_rows']),
                         trade_rows=int(v.loc[m, 'trade_rows']),
                         contaminated=m in CONTAMINATED,
                         contamination_reason=CONTAMINATED.get(m, ''),
                         reason=why))
    df = pd.DataFrame(rows).sort_values(['split', 'component', 'match'])

    os.makedirs(SPLITS, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(SPLITS, f'v{n}.csv')):
        n += 1
    out = os.path.join(SPLITS, f'v{n}.csv')
    df.to_csv(out, index=False)

    meta = dict(version=n, created_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                target=TARGET, n_matches=len(ms), n_components=len(comps),
                pinned_components=[c['id'] for c in forced],
                balance_score_rows_per_match=round(best[0][0], 1),
                tier_filter='AB')
    with open(os.path.join(SPLITS, f'v{n}.meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\nwrote {out}\n")
    s = df.groupby('split').agg(matches=('match', 'size'), components=('component', 'nunique'),
                                book_rows=('book_rows', 'sum'), trades=('trade_rows', 'sum'),
                                tierA=('tier', lambda x: (x == 'A').sum()),
                                tierB=('tier', lambda x: (x == 'B').sum()))
    s['rows_per_match'] = (s.book_rows / s.matches).round(0).astype(int)
    print(s.to_string())
    print(f"\nteam overlap between splits (must be empty):")
    tset = {k: set() for k in TARGET}
    for r in df.itertuples():
        tset[r.split].update(r.teams.split('-'))
    for a, b in itertools.combinations(tset, 2):
        ov = tset[a] & tset[b]
        print(f"  {a:10s} vs {b:10s}: {sorted(ov) if ov else 'none'}")
    print(f"\nsealed set ({len(df[df.split=='sealed'])} matches) -- DO NOT OPEN:")
    print(df[df.split == 'sealed'][['match', 'teams', 'tier', 'book_rows']].to_string(index=False))


if __name__ == '__main__':
    main()
