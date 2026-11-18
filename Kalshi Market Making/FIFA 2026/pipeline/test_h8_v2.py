"""
H8 re-run on the repaired data (2026-10-23).

The registered H8 run (2026-10-12) used the ORIGINAL 47-match build, before the
cold-start defect was found, over the v1 'explore' split (24 matches, 8 groups,
including 26JUN12CANBIH, now in the gaps tier).

This re-run uses the depth_clean tree (CANON_SUFFIX=_v2) over the v2 recombination
pool: 21 matches in 9 groups. The measurement code is unchanged -- touch_flow() is
imported from test_h8.py. The predictions are the registered ones, scaled to the
new counts at the same proportions (20/24 -> 18/21; 7/8 groups -> 8/9).

Maintenance: H8 is in-play only, and no pool match was in play during a Thursday
07:00-10:00 UTC window (checked), so the maintenance mask is a no-op here.

    CANON_SUFFIX=_v2 OFF=0 LIM=7 python3 pipeline/test_h8_v2.py run
    ... OFF=7, OFF=14 ...
    CANON_SUFFIX=_v2 python3 pipeline/test_h8_v2.py summary
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import legs, split_matches, splits
from test_h8 import touch_flow

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'canonical')


def run():
    assert os.environ.get('CANON_SUFFIX') == '_v2', 'run with CANON_SUFFIX=_v2'
    ms = split_matches('cv')
    off = int(os.environ.get('OFF', 0)); lim = int(os.environ.get('LIM', len(ms)))
    rows = []
    for m in ms[off:off + lim]:
        for lg in legs(m):
            r = touch_flow(m, lg)      # no try/except: a failure must be seen
            if r:
                rows.append(r)
        print(f'  done {m}', flush=True)
    f = os.path.join(OUT, f'h8_v2_legs_{off}.csv')
    pd.DataFrame(rows).to_csv(f, index=False)
    print(f'wrote {f} ({len(rows)} legs)')


def summary():
    d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(os.path.join(OUT, 'h8_v2_legs_*.csv')))])
    d = d.drop_duplicates(['match', 'leg'])
    sp = splits()
    assert set(d.match) <= set(sp.index[sp.split == 'cv']), 'non-pool match in output'
    # legs aggregated by volume within a match, as in the registered run
    m = d.groupby('match').agg(cancel=('cancel_vol', 'sum'), fill=('fill_vol', 'sum'),
                               med_fill=('med_fill', 'median'), med_cancel=('med_cancel', 'median'),
                               p90_cancel=('p90_cancel', 'median'), n_legs=('leg', 'size'))
    m['share'] = m.cancel / (m.cancel + m.fill)
    m['group'] = sp.loc[m.index, 'group']
    n = len(m); ng = m.group.nunique()
    g = m.groupby('group').share.median()
    q1, q3 = m.share.quantile([0.25, 0.75])

    p1 = int((m.cancel > m.fill).sum())
    p2 = int((m.med_fill < m.med_cancel).sum())
    p3 = float(m.share.median())
    p4 = int((g > 0.5).sum())
    need = int(np.ceil(20 / 24 * n)); needg = int(np.ceil(7 / 8 * ng))
    print(f'{n} matches, {len(d)} legs, {ng} groups (legs per match: {sorted(m.n_legs.unique())})\n')
    print(f'P1 cancel > trade          {p1}/{n}   need >= {need}   {"pass" if p1 >= need else "FAIL"}')
    print(f'P2 median fill < cancel    {p2}/{n}   need >= {need}   {"pass" if p2 >= need else "FAIL"}')
    print(f'P3 median cancel share     {p3:.1%}   need > 60%   {"pass" if p3 > 0.6 else "FAIL"}')
    print(f'P4 groups (median match)   {p4}/{ng}    need >= {needg}    {"pass" if p4 >= needg else "FAIL"}')
    print('\nkill criteria:')
    print(f'  share < 50% in >= 1/3 of matches : {int((m.share < 0.5).sum())} of {n}')
    print(f'  median share in 45-55%           : {0.45 <= p3 <= 0.55}')
    print(f'  IQR                              : {(q3 - q1) * 100:.1f} pp (kill if > 30)')
    print(f'  groups holding                   : {p4} of {ng} (kill if < {int(np.ceil(6 / 8 * ng))})')
    print(f'\nshare range {m.share.min():.1%} - {m.share.max():.1%}; '
          f'group medians {g.min():.1%} - {g.max():.1%}')
    print(f'median fill size {m.med_fill.min():.0f}-{m.med_fill.max():.0f}; '
          f'median cancel {m.med_cancel.min():.0f}-{m.med_cancel.max():.0f}')
    print(f'p90 cancel exactly 50,000 in {int((m.p90_cancel.round() == 50000).sum())} of {n} matches '
          f'(leg-level: {int((d.p90_cancel.round() == 50000).sum())} of {len(d)} legs)')
    print('\nper match:')
    print(m[['group', 'share', 'med_fill', 'med_cancel', 'p90_cancel']]
          .sort_values(['group', 'share']).round(3).to_string())
    m.to_csv(os.path.join(OUT, 'h8_v2_matches.csv'))


if __name__ == '__main__':
    {'run': run, 'summary': summary}[sys.argv[1]]()
