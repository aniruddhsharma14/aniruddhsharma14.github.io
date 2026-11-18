"""
Stage 1, v2 -- SPLITS FOR RECOMBINATION.

Writes splits/v<N>.csv and its .meta.json. Touches nothing else; v1 is kept.

--------------------------------------------------------------------------------
WHAT CHANGED FROM v1, AND WHY

1. The universe is the 27 depth_clean matches (canonical/leg_quality.csv), not the
   40 legacy tier-A/B matches. Four of those 40 have dropped messages and nine
   cannot support size or depth work; see H10 in hypotheses.md.

2. Groups come from the FULL 72-fixture graph. v1 built the team graph from the
   quality-filtered matches only, so a group whose linking matches had been filtered
   out split into fragments -- 13 components instead of the 12 tournament groups.
   Two fragments of one group sitting on opposite sides of a split leak that
   group's shared teams across it. Built from every fixture in the ticker universe,
   the graph gives exactly 12 groups of 4 teams and 6 matches each.

3. Shape: a recombination pool plus a sealed set, not explore / validation /
   sealed. With ~11 independent groups, a three-way split leaves every partition
   too thin to say anything. Instead every non-sealed group goes into one pool, and
   out-of-sample estimates come from many train/test recombinations of whole
   groups, giving a distribution of performance rather than one number. The sealed
   groups are spent once, at the end, as a final check.

--------------------------------------------------------------------------------
WHY THE SEALED SET IS CARRIED OVER, NOT REDRAWN

v1's sealed set was committed before any analysis and has never been used to test
a hypothesis. Choosing a new sealed set now would mean choosing it with knowledge
of the data, which is the one thing a sealed set must not be. So it is kept, on
three conditions checked below -- any failure aborts rather than adjusts:

  - it consists of WHOLE true groups (no group straddles cv and sealed);
  - no sealed match was ever in v1's explore set (where H8 was run);
  - no sealed match is on the CONTAMINATED list.

Integrity reads of sealed matches (reconstruction, Predexon comparison, consistency)
do not spend them: none of those looks at any relationship between a feature and a
future outcome, which is what a test set protects.
"""
from __future__ import annotations
import sys, os, re, json
from math import comb
from datetime import datetime, timezone
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import matches, teams, ROOT, CANON
from stage1_splits import components, CONTAMINATED

SPLITS = os.path.join(ROOT, 'splits')


def fixtures():
    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    ms = {t.split('-')[1] for t in inv.ticker if t.startswith('KXWCGAME-')}
    return sorted(m for m in ms if re.fullmatch(r'\d{2}[A-Z]{3}\d{2}[A-Z]{6}', m))


def main():
    fx = fixtures()
    comps = components(fx)
    sizes = sorted({(len(c['teams']), len(c['matches'])) for c in comps})
    if len(fx) != 72 or len(comps) != 12 or sizes != [(4, 6)]:
        raise SystemExit(f"expected 72 fixtures in 12 groups of 4 teams / 6 matches; "
                         f"got {len(fx)} fixtures, {len(comps)} groups, sizes {sizes}")
    group_of, group_teams = {}, {}
    for gid, c in enumerate(comps):
        for m in c['matches']:
            group_of[m] = gid
        group_teams[gid] = '/'.join(c['teams'])

    uni = matches('depth_clean')
    q = pd.read_csv(os.path.join(CANON, 'leg_quality.csv'))
    tier_of = q.groupby('match').tier.first()
    v1 = pd.read_csv(os.path.join(SPLITS, 'v1.csv')).set_index('match')
    val = pd.read_csv(os.path.join(CANON, 'validation_v2.csv')).set_index('match')
    win = pd.read_csv(os.path.join(CANON, 'windows.csv')).set_index('match')

    v1_sealed = set(v1.index[v1.split == 'sealed'])
    v1_explore = set(v1.index[v1.split == 'explore'])
    sealed_groups = sorted({group_of[m] for m in v1_sealed if m in group_of})

    # ---- the three conditions; abort, never adjust
    problems = []
    for m in uni:
        g = group_of[m]
        if (g in sealed_groups) != (m in v1_sealed):
            problems.append(f"{m}: group {g} straddles cv/sealed")
        if g in sealed_groups and m in v1_explore:
            problems.append(f"{m}: sealed but was in v1 explore (H8)")
        if g in sealed_groups and m in CONTAMINATED:
            problems.append(f"{m}: sealed but contaminated ({CONTAMINATED[m]})")
    if problems:
        raise SystemExit("cannot carry the sealed set over:\n  " + "\n  ".join(problems))

    rows = []
    for m in uni:
        g = group_of[m]
        a, b = teams(m)
        use = []
        if m in v1_explore:
            use.append('H8 replication (v1 explore set)')
        if m in CONTAMINATED:
            use.append(CONTAMINATED[m])
        sealed = g in sealed_groups
        rows.append(dict(
            match=m, split='sealed' if sealed else 'cv', group=g,
            group_teams=group_teams[g], teams=f'{a}-{b}',
            day=pd.to_datetime(int(win.loc[m, 'kickoff_ms']), unit='ms', utc=True)
                  .strftime('%Y-%m-%d'),
            tier=tier_of[m],
            book_rows=int(val.loc[m, 'book_rows']), trade_rows=int(val.loc[m, 'trade_rows']),
            prior_use='; '.join(use) if use else 'none',
            reason=('carried over from v1 sealed: whole group, never analysed' if sealed
                    else 'recombination pool')))
    df = pd.DataFrame(rows).sort_values(['split', 'group', 'match'])

    # matches OUTSIDE the universe that share a group with the sealed set would leak
    # those teams if someone used them for flow work; report them
    other = sorted(m for m in tier_of.index
                   if m not in uni and group_of.get(m) in sealed_groups)

    os.makedirs(SPLITS, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(SPLITS, f'v{n}.csv')):
        n += 1
    out = os.path.join(SPLITS, f'v{n}.csv')
    df.to_csv(out, index=False)

    cv = df[df.split == 'cv']
    N = cv.group.nunique()
    meta = dict(
        version=n, created_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        universe='depth_clean (canonical/leg_quality.csv)', n_matches=len(df),
        n_groups_total=len(comps), n_groups_in_universe=int(df.group.nunique()),
        sealed_groups=sealed_groups, sealed_matches=int((df.split == 'sealed').sum()),
        cv_groups=sorted(int(x) for x in cv.group.unique()), cv_matches=len(cv),
        cv_group_sizes={int(k): int(v) for k, v in cv.groupby('group').size().items()},
        recombination_example=dict(N=N, k=2, splits=comb(N, 2), paths=N - 1),
        cv_matches_with_prior_use=int((cv.prior_use != 'none').sum()),
        other_tier_matches_in_sealed_groups=other,
        rules=['groups = connected components of the full 72-fixture team graph',
               'sealed carried over from v1 only if whole groups, never explored, '
               'never contaminated; otherwise abort',
               'all other groups form the recombination pool; groups are indivisible'])
    with open(os.path.join(SPLITS, f'v{n}.meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"72 fixtures -> {len(comps)} groups of 4 teams. Universe: {len(df)} depth_clean "
          f"matches in {df.group.nunique()} groups.\n\nwrote {out}\n")
    s = df.groupby(['split', 'group', 'group_teams']).agg(
        matches=('match', 'size'), days=('day', lambda x: ','.join(sorted(set(x)))),
        prior_use=('prior_use', lambda x: int((x != 'none').sum())),
        book_rows=('book_rows', 'sum'))
    print(s.to_string())
    ct = set(); st = set()
    for r in df.itertuples():
        (st if r.split == 'sealed' else ct).update(r.teams.split('-'))
    print(f"\nteam overlap cv vs sealed (must be empty): {sorted(ct & st) or 'none'}")
    print(f"other-tier matches sharing a sealed group: {other or 'none'}")
    print(f"\nrecombination over {N} cv groups, 2 held out at a time: "
          f"{comb(N, 2)} train/test splits, {N - 1} backtest paths")
    print(f"cv matches with prior analysis use: {meta['cv_matches_with_prior_use']} of {len(cv)}")


if __name__ == '__main__':
    main()
