"""
hy_walk.py -- walk-forward cross-match correlations, written to be read.

THE QUESTION
    As the tournament goes on, do the prices of different matches move together?
    In particular:
      * same team, two different matches (e.g. ARG in ARG-DZA and ARG in ARG-AUT)
        -> should move TOGETHER (+) if the market is updating ARG's strength;
      * a team and its future opponent (ARG in ARG-DZA vs AUT in AUT-JOR, when ARG
        and AUT meet later) -> should move OPPOSITELY (-): good news for ARG is bad
        news for whoever plays ARG.

    We compute the correlation matrix in 3-day windows that slide forward one day at
    a time. A match that has finished drops out of later windows automatically.

HOW TO RUN (from the data folder, i.e. ~/Desktop/worldcup/FIFA/data)
    python3 pipeline/learn/hy_walk.py selftest   # checks the estimator on fake data
    python3 pipeline/learn/hy_walk.py walk       # the real thing -> canonical/mine/
    Or open notebooks/walk_forward.ipynb, which walks through it step by step.

READING ORDER
    1. load_quotes      -- raw best bid/ask -> clean mid prices
    2. fixture_groups   -- which matches belong to which tournament group
    3. match_info       -- when each match ended (and which "ends" we can trust)
    4. build_series     -- one price path per (match, team), transformed
    5. Increments       -- turn a path into a list of price CHANGES
    6. hy / hy_brute    -- the Hayashi-Yoshida correlation estimator
    7. selftest         -- prove the estimator works before trusting it
    8. pair_class       -- label each pair (same team, future opponent, ...)
    9. walk             -- slide the window, compute every pair, save
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ paths
# This file lives in <data>/pipeline/learn/, so the data folder is 3 levels up.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANON = os.path.join(ROOT, 'canonical')
TICK = os.path.join(CANON, 'ticker')          # exchange best bid/ask, one file per day
OUT = os.path.join(CANON, 'mine')             # YOUR outputs go here, never over mine

# ------------------------------------------------------------------ constants
# All times in this project are integers: milliseconds since 1970-01-01 UTC.
HOUR = 3_600_000
DAY = 24 * HOUR


def utc_ms(s):
    """'2026-06-16 23:08' (UTC) -> milliseconds. Small helper so constants read well."""
    return pd.Timestamp(s, tz='UTC').value // 10**6


# The recorder was off from June 16 23:08 to June 22. Matches were played and
# settled inside that hole. If we ever computed a price change ACROSS it, that one
# giant jump would dominate every correlation. So no increment may bridge it.
GAP = (utc_ms('2026-06-16 23:08'), utc_ms('2026-06-22 00:00'))

# Kalshi pauses every Thursday 03:00-05:00 ET (07:00-09:00 UTC in June) for
# maintenance. Orders get cancelled and re-posted, so prices across ALL markets
# jump and snap back together. That fakes positive correlation everywhere. We add
# the hour after it (re-quoting) and optionally drop all of it.
MAINT = [(utc_ms(f'{d} 07:00'), utc_ms(f'{d} 10:00'))
         for d in ('2026-06-04', '2026-06-11', '2026-06-18', '2026-06-25')]

MAX_SPREAD = 50.0       # cents. A quote this wide is not a price (see load_quotes).
MIN_INCREMENTS = 5      # fewer price changes than this in a window -> no estimate
MIN_OVERLAP = 6 * HOUR  # two series must be live together this long to be compared
WINDOW = 3 * DAY        # length of each walk-forward window


# ================================================================== 1. load
def load_quotes():
    """Read every daily ticker file and return a clean table of mid prices.

    Each row of the raw data is the exchange telling us "the best bid and best ask
    for this contract are now X and Y". We keep:
        ts    when (ms UTC)
        match e.g. 26JUN16ARGDZA  (date + the two teams)
        leg   which outcome: a team code (ARG, DZA) or TIE
        mid   (bid + ask) / 2, in cents = the market's probability estimate x 100
    """
    files = sorted(glob.glob(os.path.join(TICK, '2026-*.parquet')))
    # Read only the columns we need -- these files are big.
    d = pd.concat([pd.read_parquet(f, columns=['ts', 'match', 'leg', 'bid', 'ask'])
                   for f in files], ignore_index=True)
    n_raw = len(d)

    # A side of the book can be empty (NaN or 0). No two-sided quote -> no mid.
    d = d[d.bid.notna() & d.ask.notna() & (d.bid > 0) & (d.ask > 0)]

    # After a match settles, makers pull their orders and the book collapses to
    # something like bid 1 / ask 99. Its "mid" is 50 -- a fake jump at the end of
    # every match. Requiring spread < 50c removes those.
    n_two_sided = len(d)
    d = d[(d.ask - d.bid) < MAX_SPREAD]
    print(f'quotes: {n_raw:,} raw -> {n_two_sided:,} two-sided -> {len(d):,} '
          f'with spread < {MAX_SPREAD:.0f}c')

    d = d.assign(mid=(d.bid + d.ask) / 2)
    # Stable sort keeps the original order for rows with identical timestamps.
    d = d.sort_values('ts', kind='stable').reset_index(drop=True)

    # Safety: the sealed set (groups F and G) must never be here. Extraction already
    # removed it; we check anyway, because spending the sealed set is unrecoverable.
    sealed_teams = {'JPN', 'NED', 'SWE', 'TUN', 'BEL', 'EGY', 'IRI', 'NZL'}
    teams = set(d.match.str[7:10]) | set(d.match.str[10:13])
    assert not (teams & sealed_teams), f'sealed teams in data: {teams & sealed_teams}'
    return d[['ts', 'match', 'leg', 'mid']]


# ================================================================== 2. groups
def fixture_groups():
    """{match: group_id}, worked out from the fixture list itself.

    Think of teams as dots and each fixture as a line joining two dots. A group is
    a connected cluster of dots. We find clusters with "union-find": every team
    starts as its own cluster; for each fixture we merge the two teams' clusters.

    We must use ALL 72 group-stage fixtures (from inventory.csv), not just the ones
    we recorded well -- with missing lines a group could split in two.
    """
    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    game_tickers = inv.ticker[inv.ticker.str.startswith('KXWCGAME')]
    # 'KXWCGAME-26JUN16ARGDZA-ARG' -> '26JUN16ARGDZA'
    matches = sorted(game_tickers.str.split('-').str[1].unique())

    parent = {}

    def find(team):
        parent.setdefault(team, team)
        while parent[team] != team:          # walk up to the cluster's root
            team = parent[team]
        return team

    for m in matches:
        home, away = m[7:10], m[10:13]
        parent[find(home)] = find(away)      # merge the two clusters

    return {m: find(m[7:10]) for m in matches}


# ================================================================== 3. match info
def match_info(quotes):
    """{match: dict(group, first, end)} -- when each match's prices start and stop.

    `end` matters: after the final whistle a match's price is just 0 or 100, so it
    must leave the live set.

    windows.csv has kickoff/end times, BUT 21 of its fixtures carry a fake 21:00
    kickoff: the tape stopped before they were played (June 16, or inside the gap).
    So we only trust windows.csv's end time when we can show the match was actually
    played on tape:
        (a) `covered` is True in windows.csv, or
        (b) the final recorded trades show a settled match (one leg >= 98c, the
            other two <= 2c) AND kickoff and end both fall on days we recorded.
    (a)/(b) are needed together because a match played INSIDE the gap still looks
    settled on the June 22 tape -- the ticker shows the last trade price.
    Otherwise, end = the last quote we have.
    """
    w = pd.read_csv(os.path.join(CANON, 'windows.csv')).set_index('match')
    groups = fixture_groups()

    # --- (b) part 1: did the match settle? Final trade price per leg.
    files = sorted(glob.glob(os.path.join(TICK, '2026-*.parquet')))
    trades = pd.concat([pd.read_parquet(f, columns=['ts', 'match', 'leg', 'last'])
                        for f in files])
    final = (trades.dropna(subset=['last']).sort_values('ts')
                   .groupby(['match', 'leg'])['last'].last())
    settled = {}
    for m, prices in final.groupby(level=0):
        v = sorted(prices.values)            # [lowest, middle, highest]
        settled[m] = len(v) == 3 and v[2] >= 98 and v[1] <= 2 and v[0] <= 2

    # --- (b) part 2: is the in-play window on a day we recorded?
    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    inv['day'] = inv.file.str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
    tape_end = inv.groupby('day').ts_last.max().to_dict()   # last ms recorded per day

    def on_tape(row):
        k_day = pd.to_datetime(row.kickoff_ms, unit='ms', utc=True).strftime('%Y-%m-%d')
        e_day = pd.to_datetime(row.end_ms, unit='ms', utc=True).strftime('%Y-%m-%d')
        return (k_day in tape_end and e_day in tape_end
                and row.end_ms <= tape_end[e_day] + 60_000)

    info = {}
    for m, g in quotes.groupby('match'):
        first, last = int(g.ts.min()), int(g.ts.max())
        trusted = m in w.index and bool(w.loc[m, 'covered'] or
                                        (settled.get(m, False) and on_tape(w.loc[m])))
        end = int(w.loc[m, 'end_ms']) if trusted else last
        info[m] = dict(group=groups[m], first=first, end=end, trusted_end=trusted)
    print(f'matches: {len(info)} in {len(set(v["group"] for v in info.values()))} groups; '
          f'{sum(v["trusted_end"] for v in info.values())} with a trusted final whistle')
    return info


# ================================================================== 4. series
def arcsine(mid):
    """Probability transform: 2*asin(sqrt(p)).

    Why transform at all? A 1c move means more near 5c than near 50c. This
    transform stretches the ends so a "unit" of movement is comparable everywhere
    (it's the variance-stabilising transform for a probability).
    """
    p = np.clip(mid / 100.0, 0.0, 1.0)
    return 2 * np.arcsin(np.sqrt(p))


def build_series(quotes, info, mask_maint=False):
    """{(match, team): dict(inc=Increments, lo, hi, group, opp)}

    One series per team per match: that team's WIN leg. TIE legs are dropped -- a
    team's strength lives in its win price, and the tie leg would double count.
    """
    out = {}
    for (m, leg), g in quotes.groupby(['match', 'leg'], sort=False):
        home, away = m[7:10], m[10:13]
        if leg not in (home, away):
            continue                         # the TIE leg
        v = info[m]
        t = g.ts.to_numpy(np.int64)
        mid = g.mid.to_numpy(np.float64)
        live = (t >= v['first']) & (t <= v['end'])   # cut everything after the whistle
        t, mid = t[live], mid[live]
        if len(t) < 10:
            continue
        out[(m, leg)] = dict(inc=Increments(t, arcsine(mid), mask_maint),
                             lo=int(t[0]), hi=v['end'], group=v['group'],
                             opp=away if leg == home else home)
    print(f'series: {len(out)} (match, team) win legs')
    return out


# ================================================================== 5. increments
class Increments:
    """A price path stored as its CHANGES.

    A quote stream repeats the same price many times. Only changes carry
    information, so we keep change points t0 < t1 < t2 ... and store

        interval i = (a_i, b_i] = (t_i, t_{i+1}]      dx_i = x_{i+1} - x_i

    "The price moved by dx_i somewhere between a_i and b_i." We don't know exactly
    when inside the interval -- which is why Hayashi-Yoshida (below) counts every
    overlap rather than pretending the move happened at one instant.
    """
    def __init__(self, t, x, mask_maint=False):
        changed = np.r_[True, np.diff(x) != 0]       # first point + every change
        t, x = t[changed], x[changed]
        a, b, dx = t[:-1], t[1:], np.diff(x)

        # Two intervals [s0, s1] and (a, b] overlap when a < s1 and b > s0.
        drop = (a < GAP[1]) & (b > GAP[0])           # never bridge the recording gap
        if mask_maint:
            for s0, s1 in MAINT:
                drop |= (a < s1) & (b > s0)
        self.a, self.b, self.dx = a[~drop], b[~drop], dx[~drop]

    def inside(self, lo, hi):
        """Increments that lie fully inside [lo, hi]."""
        m = (self.a >= lo) & (self.b <= hi)
        return self.a[m], self.b[m], self.dx[m]


# ================================================================== 6. estimator
def hy_brute(x, y, lo, hi):
    """Hayashi-Yoshida, the obvious way. Slow -- used only to check `hy`.

    cov = sum over every (i, j) whose intervals overlap of dx_i * dy_j
    rho = cov / sqrt(sum dx^2 * sum dy^2)

    Why this works when the two series update at different times: if X moved in
    (a_i, b_i] and Y moved in (c_j, d_j] and those intervals overlap, the two moves
    COULD share a cause. If they don't overlap, they can't. A naive approach (sample
    both every minute, correlate) instead sees mostly zero changes for the slow
    series and drags the correlation toward 0 -- the "Epps effect".
    """
    a, b, dx = x.inside(lo, hi)
    c, d, dy = y.inside(lo, hi)
    if len(dx) < MIN_INCREMENTS or len(dy) < MIN_INCREMENTS:
        return np.nan
    cov = 0.0
    for i in range(len(dx)):
        for j in range(len(dy)):
            if c[j] < b[i] and d[j] > a[i]:          # the two intervals overlap
                cov += dx[i] * dy[j]
    den = np.sqrt(np.sum(dx ** 2) * np.sum(dy ** 2))
    return cov / den if den > 0 else np.nan


def hy(x, y, lo, hi):
    """Same answer as hy_brute, fast. Returns (rho, n_x, n_y).

    Trick: Y's intervals are sorted and touch end to end. So for X's interval
    (a_i, b_i], the Y intervals that overlap it are a CONTIGUOUS block j0 .. j1-1:
        j0 = first j with d_j > a_i   -> np.searchsorted(d, a_i, 'right')
        j1 = first j with c_j >= b_i  -> np.searchsorted(c, b_i, 'left')
    and the sum of dy over a block is a difference of a cumulative sum:
        sum(dy[j0:j1]) = cs[j1] - cs[j0],   cs = [0, dy0, dy0+dy1, ...]
    That turns the double loop into a few vector operations.
    """
    a, b, dx = x.inside(lo, hi)
    c, d, dy = y.inside(lo, hi)
    if len(dx) < MIN_INCREMENTS or len(dy) < MIN_INCREMENTS:
        return np.nan, len(dx), len(dy)
    j0 = np.searchsorted(d, a, side='right')
    j1 = np.searchsorted(c, b, side='left')
    cs = np.r_[0.0, np.cumsum(dy)]
    cov = float(np.sum(dx * (cs[j1] - cs[j0])))
    den = np.sqrt(np.sum(dx ** 2) * np.sum(dy ** 2))
    return (cov / den if den > 0 else np.nan), len(dx), len(dy)


# ================================================================== 7. self-test
def simulate(rho, rng, days=10):
    """Two correlated random walks with KNOWN correlation rho.

    X is observed every 10 s (a busy market). Y is observed about once an hour at
    random times and held flat in between (a quiet market with a stale quote).
    Returns (tx, X_obs, ty, Y_obs).
    """
    n = days * 86_400 // 10                    # number of 10-second steps
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + np.sqrt(1 - rho ** 2) * rng.standard_normal(n)   # corr(z1,z2)=rho
    t = np.arange(n, dtype=np.int64) * 10_000  # ms
    X, Y = np.cumsum(z1), np.cumsum(z2)
    ty = np.sort(rng.choice(t, size=days * 24, replace=False))
    return t, X, ty, Y[np.searchsorted(t, ty)]


def selftest():
    rng = np.random.default_rng(20261016)

    print('1) hy (fast) == hy_brute (slow) on small random series')
    worst = 0.0
    for _ in range(20):
        tx = np.sort(rng.choice(10**6, 60, replace=False)).astype(np.int64)
        ty = np.sort(rng.choice(10**6, 40, replace=False)).astype(np.int64)
        X = Increments(tx, np.cumsum(rng.standard_normal(60)))
        Y = Increments(ty, np.cumsum(rng.standard_normal(40)))
        worst = max(worst, abs(hy(X, Y, 0, 10**6)[0] - hy_brute(X, Y, 0, 10**6)))
    print(f'   largest difference {worst:.1e}  ->  {"PASS" if worst < 1e-12 else "FAIL"}')

    print('2) HY recovers a planted correlation; a 1-minute grid does not (Epps)')
    for rho in (0.6, 0.0, -0.5):
        tx, X, ty, Y = simulate(rho, rng)
        r, nx, ny = hy(Increments(tx, X), Increments(ty, Y), 0, tx[-1])
        grid = np.arange(0, tx[-1], 60_000)
        gx = X[np.searchsorted(tx, grid)]
        gy = Y[np.clip(np.searchsorted(ty, grid, 'right') - 1, 0, None)]   # last seen
        pearson = np.corrcoef(np.diff(gx), np.diff(gy))[0, 1]
        # How close is "close"? The estimate's noise is set by the SLOW series: with
        # n_y changes the standard error is roughly 1/sqrt(n_y) (~0.065 for 239).
        # Allow 3 standard errors; a fixed tolerance like 0.1 fails by bad luck.
        tol = 3 / np.sqrt(ny)
        ok = abs(r - rho) < tol
        print(f'   true {rho:+.2f}   HY {r:+.3f}   grid Pearson {pearson:+.3f}   '
              f'({nx:,} vs {ny} changes, tolerance {tol:.2f})  ->  {"PASS" if ok else "FAIL"}')

    print('3) unbiased at zero: mean of HY over 200 runs with rho = 0')
    rs = []
    for _ in range(200):
        tx, X, ty, Y = simulate(0.0, rng, days=3)
        rs.append(hy(Increments(tx, X), Increments(ty, Y), 0, tx[-1])[0])
    se = np.std(rs) / np.sqrt(len(rs))
    print(f'   mean {np.mean(rs):+.4f} (standard error {se:.4f})  ->  '
          f'{"PASS" if abs(np.mean(rs)) < 3 * se else "FAIL"}')


# ================================================================== 8. classes
def pair_class(k1, k2, series):
    """Label a pair of series. k = (match, team)."""
    (m1, x), (m2, y) = k1, k2
    if m1 == m2:
        return 'same_match'          # mechanically linked legs -- always skipped
    if x == y:
        return 'same_team'           # prediction: +
    if series[k1]['group'] != series[k2]['group']:
        return 'cross_group'         # prediction: ~0 (the baseline)
    if series[k1]['opp'] == y or series[k2]['opp'] == x:
        return 'future_opponent'     # x and y play each other in m1 or m2: -
    return 'same_group_other'        # prediction: ~0


# ================================================================== 9. walk
def walk(series, start='2026-06-02', end='2026-06-28'):
    """Slide a WINDOW-long window forward a day at a time; HY for every live pair."""
    keys = sorted(series)
    rows = []
    for day in pd.date_range(start, end, freq='D', tz='UTC'):
        lo = day.value // 10**6
        hi = lo + WINDOW
        # live = the series' life overlaps the window (finished matches fall out)
        live = [k for k in keys if series[k]['lo'] < hi and series[k]['hi'] > lo]
        for i, k1 in enumerate(live):
            for k2 in live[i + 1:]:
                cls = pair_class(k1, k2, series)
                if cls == 'same_match':
                    continue
                s1, s2 = series[k1], series[k2]
                w0, w1 = max(lo, s1['lo'], s2['lo']), min(hi, s1['hi'], s2['hi'])
                if w1 - w0 < MIN_OVERLAP:
                    continue
                r, n1, n2 = hy(s1['inc'], s2['inc'], w0, w1)
                if np.isfinite(r):
                    rows.append(dict(window_start=day.strftime('%Y-%m-%d'),
                                     a=f'{k1[0]}:{k1[1]}', b=f'{k2[0]}:{k2[1]}',
                                     cls=cls, rho=r, nx=n1, ny=n2))
        print(f'  window {day:%m-%d}: {len(live)} live series', flush=True)
    return pd.DataFrame(rows)


def run_walk(mask_maint=False):
    quotes = load_quotes()
    info = match_info(quotes)
    series = build_series(quotes, info, mask_maint=mask_maint)
    w = walk(series)
    os.makedirs(OUT, exist_ok=True)
    name = 'walk_forward_maintmask.parquet' if mask_maint else 'walk_forward.parquet'
    w.to_parquet(os.path.join(OUT, name), index=False)
    print(f'\nsaved {len(w):,} pair-window estimates to canonical/mine/{name}')
    print('\nmean rho by window and class:')
    print(w.pivot_table(index='window_start', columns='cls', values='rho',
                        aggfunc='mean').round(3).to_string())
    return w


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'selftest'
    if cmd == 'selftest':
        selftest()
    elif cmd == 'walk':
        run_walk(mask_maint='--mask-maint' in sys.argv)
    else:
        sys.exit('usage: hy_walk.py [selftest | walk [--mask-maint]]')
