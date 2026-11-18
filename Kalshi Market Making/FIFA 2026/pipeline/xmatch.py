"""
Cross-match price dependence (H11, hypotheses.md).

    python3 pipeline/xmatch.py corr      # descriptive: rolling correlation matrices
    python3 pipeline/xmatch.py events    # the registered primary test

Source: canonical/ticker/*.parquet (exchange-published best bid/ask; sealed groups
already removed at extraction). Mid = (bid + ask) / 2, two-sided quotes only.

CORR is descriptive, as registered. One series per match -- the first-listed
team's win leg -- so the three legs of a match, which sum to ~100 mechanically,
cannot manufacture correlation. Per tape day, non-overlapping h-minute changes at
h = 1, 5, 15, pairwise over the bars where both matches are live. A match leaves
the live set at its final whistle.

EVENTS implements the H11 primary test as registered, with one interpretation
made explicit here because the registration left it implicit: "exceeds the 95th
percentile of its own placebo distribution" is read as the distribution of the
MEAN abnormal response over the same number of randomly timed placebo events,
resampled 2,000 times. Comparing a mean against single placebo draws would make
every pair look significant.
"""
from __future__ import annotations
import os, sys, glob, json
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')
TICK = os.path.join(CANON, 'ticker')
OUT = os.path.join(CANON, 'xmatch')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ticker_extract import groups_of_fixtures

MIN = 60_000
RNG = np.random.default_rng(20261016)          # fixed seed: results are reproducible

# ---- registered parameters (H11) ------------------------------------------
JUMP_C, JUMP_S = 10.0, 60          # event: any A leg moves >= 10c within 60 s
DEBOUNCE_S = 600                   # one event per 10 minutes
RESP_S = 300                       # response window 5 min, pre window 5 min
PLACEBO_GAP_S = 600                # placebo times >= 10 min from any event
N_BOOT = 2000
MAX_SPREAD = 50.0                  # cents; wider quotes carry no usable mid


# ---------------------------------------------------------------- loading
def load():
    fs = sorted(glob.glob(os.path.join(TICK, '2026-*.parquet')))
    d = pd.concat([pd.read_parquet(f, columns=['ts', 'match', 'leg', 'bid', 'ask'])
                   for f in fs], ignore_index=True)
    d = d[(d.bid > 0) & (d.ask > 0) & d.bid.notna() & d.ask.notna()]
    # A mid from a near-empty book is not a price. After settlement the quotes
    # collapse (e.g. bid 1 / ask 99 -> mid 50) 11-40 s BEFORE the recorded end time,
    # which would put a fake 'jump' at the end of every match. Decided before results.
    n0 = len(d)
    d = d[(d.ask - d.bid) < MAX_SPREAD]
    print(f'spread filter (< {MAX_SPREAD:.0f}c) removed {n0 - len(d):,} of {n0:,} quotes', flush=True)
    d['mid'] = ((d.bid + d.ask) / 2).astype('float32')
    d = d.sort_values('ts', kind='stable')
    series = {}
    for (m, l), g in d.groupby(['match', 'leg'], sort=False):
        series[(m, l)] = (g.ts.to_numpy(np.int64), g.mid.to_numpy(np.float32))
    return series


def mid_at(s, t):
    """Step-function lookup: last mid at or before t. NaN before the first quote."""
    ts, mid = s
    i = np.searchsorted(ts, t, side='right') - 1
    out = np.where(i >= 0, mid[np.clip(i, 0, None)], np.nan)
    return out.astype('float64')


def inplay_recorded(w):
    """Matches whose IN-PLAY window (kickoff -> final whistle) is fully on tape.

    `covered` from windows.csv also requires an hour of settlement padding after
    the whistle. 26JUN27DZAAUT / JORARG fail `covered` only because the June 28
    tape stops at 04:01:30 -- after the final whistle, before the padding ends.

    A non-covered match is admitted ONLY on evidence that it finished on tape: its
    final recorded trades show one leg at >= 98c and the other two at <= 2c.
    Timestamps cannot decide this. windows.csv gives 21 fixtures a fake 21:00
    kickoff because their trade flow stopped with the tape (truncated June 16
    matches, and June 17-21 fixtures inside the recording gap); a date-based guard
    admitted two of them. Settlement evidence rejects all of them."""
    fs = sorted(glob.glob(os.path.join(TICK, '2026-*.parquet')))
    t = pd.concat([pd.read_parquet(f, columns=['ts', 'match', 'leg', 'last']) for f in fs])
    last = (t.dropna(subset=['last']).sort_values('ts')
             .groupby(['match', 'leg'])['last'].last())     # final recorded trade per leg
    settled = {}
    for m, g in last.groupby(level=0):
        v = sorted(g.values)
        settled[m] = len(v) == 3 and v[-1] >= 98 and v[0] <= 2 and v[1] <= 2
    # Settlement alone is NOT sufficient: the ticker channel reports the last trade
    # PRICE, so a match played inside the June 17-21 gap (26JUN21URUCPV) looks
    # settled on the June 22 tape. The in-play window itself must also be on tape.
    inv = pd.read_csv(os.path.join(CANON, 'inventory.csv'))
    inv['day'] = inv.file.str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
    data_end = inv.groupby('day').ts_last.max().to_dict()

    def on_tape(r):
        k = pd.to_datetime(int(r.kickoff_ms), unit='ms', utc=True).strftime('%Y-%m-%d')
        e = pd.to_datetime(int(r.end_ms), unit='ms', utc=True).strftime('%Y-%m-%d')
        return k in data_end and e in data_end and int(r.end_ms) <= data_end[e] + 60_000
    return {m: bool(r.covered or (settled.get(m, False) and on_tape(r)))
            for m, r in w.iterrows()}


def meta(series):
    w = pd.read_csv(os.path.join(CANON, 'windows.csv')).set_index('match')
    rec = inplay_recorded(w)
    grp = groups_of_fixtures()
    legs = {}
    for (m, l) in series:
        legs.setdefault(m, []).append(l)
    info = {}
    for m, ls in legs.items():
        last = max(series[(m, l)][0][-1] for l in ls)
        first = min(series[(m, l)][0][0] for l in ls)
        cov = rec.get(m, False)          # in-play window fully recorded (see above)
        # uncovered fixtures carry a PLACEHOLDER kickoff in windows.csv (June 16
        # 21:00) -- never use it; they were played inside the recording gap
        ko = int(w.loc[m, 'kickoff_ms']) if cov else None
        end = int(w.loc[m, 'end_ms']) if cov else last
        info[m] = dict(group=grp[m], legs=sorted(ls), team1=m[7:10], first=first,
                       end=end, kickoff=ko, covered=cov)
    return info


# ---------------------------------------------------------------- CORR
def corr(series, info):
    os.makedirs(OUT, exist_ok=True)
    # every recorded tape day -- not the days on which some series starts or ends
    days = sorted(os.path.basename(f)[:10]
                  for f in glob.glob(os.path.join(TICK, '2026-*.parquet')))
    rows = []
    for day in days:
        d0 = int(pd.Timestamp(day, tz='UTC').value // 1_000_000)
        grid = d0 + MIN * np.arange(0, 1441)
        live = [m for m, v in info.items() if v['first'] < grid[-1] and v['end'] > grid[0]
                and v['team1'] in v['legs']]
        if len(live) < 2:
            continue
        mids = {}
        for m in live:
            x = mid_at(series[(m, info[m]['team1'])], grid)
            alive = (grid >= info[m]['first']) & (grid <= info[m]['end'])
            x[~alive] = np.nan
            mids[m] = x
        status = {m: ('today' if info[m]['kickoff'] is not None and
                      pd.to_datetime(info[m]['kickoff'], unit='ms', utc=True)
                      .strftime('%Y-%m-%d') == day else 'future') for m in live}
        for h in (1, 5, 15):
            dx = {m: (v[h::h] - v[:-h:h])[:1440 // h] for m, v in mids.items()}
            ms = sorted(dx)
            for i, a in enumerate(ms):
                for b in ms[i + 1:]:
                    x, y = dx[a], dx[b]
                    ok = ~np.isnan(x) & ~np.isnan(y)
                    if ok.sum() < 30 or (x[ok] != 0).sum() < 10 or (y[ok] != 0).sum() < 10:
                        continue
                    c = np.corrcoef(x[ok], y[ok])[0, 1]
                    if not np.isfinite(c):
                        continue
                    sa, sb = sorted((status[a], status[b]))
                    rows.append(dict(day=day, h=h, a=a, b=b, corr=float(c), n=int(ok.sum()),
                                     same_group=info[a]['group'] == info[b]['group'],
                                     status=f'{sa}-{sb}'))
        print(f'  {day}: {len(live)} live matches', flush=True)
    r = pd.DataFrame(rows)
    r.to_parquet(os.path.join(OUT, 'corr_pairs.parquet'), index=False)
    print(f'\nwrote {len(r):,} pair-day-horizon correlations')
    s = (r.assign(rel=np.where(r.same_group, 'same group', 'cross group'))
          .groupby(['h', 'status', 'rel'])['corr']
          .agg(pairs='size', median='median', mean_abs=lambda v: v.abs().mean())
          .round(3))
    print(s.to_string())
    s.to_csv(os.path.join(OUT, 'corr_summary.csv'))


# ---------------------------------------------------------------- EVENTS
def leg_mids(series, info, m, t):
    return np.vstack([mid_at(series[(m, l)], t) for l in info[m]['legs']])


def jumps_in(series, info, m, t0, t1):
    """Event times in [t0, t1]: any leg moves >= JUMP_C within JUMP_S, debounced."""
    t = np.arange(t0 + JUMP_S * 1000, t1 + 1, 1000, dtype=np.int64)
    if not len(t):
        return np.array([], dtype=np.int64)
    a, b = leg_mids(series, info, m, t), leg_mids(series, info, m, t - JUMP_S * 1000)
    hit = np.nanmax(np.abs(a - b), axis=0) >= JUMP_C
    ev, last = [], -10**18
    for x in t[hit]:
        if x - last >= DEBOUNCE_S * 1000:
            ev.append(x); last = x
    return np.array(ev, dtype=np.int64)


def b_jumps_near(series, info, b, t):
    """True where B itself jumps >= JUMP_C at any second within +-60 s of t."""
    off = np.arange(-60, 61) * 1000
    tt = (t[:, None] + off[None, :]).ravel()
    a = leg_mids(series, info, b, tt)
    p = leg_mids(series, info, b, tt - JUMP_S * 1000)
    hit = np.nanmax(np.abs(a - p), axis=0) >= JUMP_C
    return hit.reshape(len(t), len(off)).any(axis=1)


def abnormal(series, info, b, t):
    """R_post - R_pre for match B at times t. NaN where B is not live throughout."""
    pre, now, post = t - RESP_S * 1000, t, t + RESP_S * 1000
    live = (pre >= info[b]['first']) & (post <= info[b]['end'])
    m0, m1, m2 = (leg_mids(series, info, b, x) for x in (pre, now, post))
    r_post = np.nanmax(np.abs(m2 - m1), axis=0)
    r_pre = np.nanmax(np.abs(m1 - m0), axis=0)
    ab = r_post - r_pre
    ab[~live] = np.nan
    ab[b_jumps_near(series, info, b, t)] = np.nan       # coincident goals dropped
    return ab


def placebo_times(series, info, a, events):
    k0, k1 = info[a]['kickoff'] + RESP_S * 1000, info[a]['end'] - RESP_S * 1000
    cand = np.arange(k0, k1, 30_000, dtype=np.int64)
    if len(events):
        far = np.min(np.abs(cand[:, None] - events[None, :]), axis=1) >= PLACEBO_GAP_S * 1000
        cand = cand[far]
    return cand


def test_stat(ev_vals, pl_vals, n):
    """Positive if mean over events exceeds 95th pct of means of n random placebos."""
    ev_vals = ev_vals[np.isfinite(ev_vals)]
    pl_vals = pl_vals[np.isfinite(pl_vals)]
    if len(ev_vals) == 0 or len(pl_vals) < 20:
        return dict(n_ev=len(ev_vals), mean=np.nan, p95=np.nan, positive=None)
    k = len(ev_vals)
    boot = RNG.choice(pl_vals, size=(N_BOOT, k), replace=True).mean(axis=1)
    p95 = float(np.quantile(boot, 0.95))
    mean = float(ev_vals.mean())
    return dict(n_ev=k, mean=mean, p95=p95, p_value=float((boot >= mean).mean()),
                positive=bool(mean > p95))


PAIRS = [('26JUN27CODUZB', '26JUN27COLPOR'), ('26JUN25CUWCIV', '26JUN25ECUGER'),
         ('26JUN27DZAAUT', '26JUN27JORARG'), ('26JUN26NORFRA', '26JUN26SENIRQ'),
         ('26JUN27CROGHA', '26JUN27PANENG'), ('26JUN25PARAUS', '26JUN25TURUSA'),
         ('26JUN24CZEMEX', '26JUN24RSAKOR'), ('26JUN24MARHTI', '26JUN24SCOBRA'),
         ('26JUN24BIHQAT', '26JUN24SUICAN'), ('26JUN26CPVKSA', '26JUN26URUESP')]


def events(series, info):
    os.makedirs(OUT, exist_ok=True)
    played = [m for m, v in info.items() if v['covered']]
    ev = {m: jumps_in(series, info, m, info[m]['kickoff'], info[m]['end']) for m in played}
    print(f'events detected: {sum(len(v) for v in ev.values())} across {len(played)} '
          f'covered matches (median {np.median([len(v) for v in ev.values()]):.0f} per match)')

    # ---- P1: simultaneous same-group pairs, both directions pooled
    p1 = []
    for a, b in PAIRS:
        evs, pls = [], []
        for x, y in ((a, b), (b, a)):
            evs.append(abnormal(series, info, y, ev[x]))
            pls.append(abnormal(series, info, y, placebo_times(series, info, x, ev[x])))
        r = test_stat(np.concatenate(evs), np.concatenate(pls), None)
        r.update(pair=f'{a} + {b}', group=info[a]['group'])
        p1.append(r)
    p1 = pd.DataFrame(p1)

    # ---- P2: cross-group; P3: same-group future (secondary)
    # The unit resampled is the EVENT TIME, not the (event, B) pair: one market-wide
    # move hits every B at once, so B responses at the same instant are correlated.
    # Average across B at each time first, then bootstrap over times.
    groups = sorted({info[m]['group'] for m in played})
    p2, p3_vals, x_vals = [], [], []
    for g in groups:
        A = [m for m in played if info[m]['group'] == g]
        evs, pls = [], []
        for a in A:
            k0, k1 = info[a]['kickoff'], info[a]['end']
            pt = placebo_times(series, info, a, ev[a])
            Be, Bp = [], []
            for b, v in info.items():
                if v['first'] >= k1 or v['end'] <= k0:
                    continue                                   # not live during A
                if v['group'] == g:
                    if b != a and (v['kickoff'] is None or v['kickoff'] > k1):
                        p3_vals.append(abnormal(series, info, b, ev[a]))    # same-group future
                    continue
                e = abnormal(series, info, b, ev[a]); Be.append(e); x_vals.append(e)
                Bp.append(abnormal(series, info, b, pt))
            if Be:
                with np.errstate(all='ignore'):
                    evs.append(np.nanmean(np.vstack(Be), axis=0) if len(ev[a]) else np.array([]))
                    pls.append(np.nanmean(np.vstack(Bp), axis=0) if len(pt) else np.array([]))
        if evs:
            r = test_stat(np.concatenate(evs), np.concatenate(pls), None)
            r.update(a_group=g, a_matches=len(A))
            p2.append(r)
    p2 = pd.DataFrame(p2)

    p3 = np.concatenate(p3_vals) if p3_vals else np.array([])
    xg = np.concatenate(x_vals) if x_vals else np.array([])
    p1.to_csv(os.path.join(OUT, 'h11_p1_pairs.csv'), index=False)
    p2.to_csv(os.path.join(OUT, 'h11_p2_crossgroup.csv'), index=False)

    print('\n=== P1  simultaneous same-group pairs  (predicted >= 7 of 10 positive) ===')
    print(p1[['pair', 'n_ev', 'mean', 'p95', 'p_value', 'positive']].round(3).to_string(index=False))
    n1 = int(p1.positive.fillna(False).sum())
    print(f'  POSITIVE: {n1} of {len(p1)}')

    print('\n=== P2  cross-group  (predicted <= 2 of 10 A-groups positive) ===')
    print(p2[['a_group', 'a_matches', 'n_ev', 'mean', 'p95', 'p_value', 'positive']]
          .round(4).to_string(index=False))
    n2 = int(p2.positive.fillna(False).sum())
    print(f'  POSITIVE: {n2} of {len(p2)}')

    print('\n=== P3  (secondary) same-group future vs cross-group, abnormal response ===')
    print(f'  same-group future: median {np.nanmedian(p3):.3f}c   mean {np.nanmean(p3):.3f}c   n={np.isfinite(p3).sum():,}')
    print(f'  cross-group      : median {np.nanmedian(xg):.3f}c   mean {np.nanmean(xg):.3f}c   n={np.isfinite(xg).sum():,}')

    verdict = ('SUPPORTED' if n1 >= 7 and n2 <= 2 else
               'REJECTED (P1 failed)' if n1 <= 6 and n2 <= 2 else
               'REJECTED (P2 failed: dependence is not group-specific)' if n1 >= 7 else
               'REJECTED (P1 and P2 both failed)')
    print(f'\n=== H11 VERDICT: {verdict} ===')
    json.dump(dict(p1_positive=n1, p1_n=len(p1), p2_positive=n2, p2_n=len(p2),
                   p3_same_future_median=float(np.nanmedian(p3)) if len(p3) else None,
                   p3_cross_median=float(np.nanmedian(xg)) if len(xg) else None,
                   verdict=verdict), open(os.path.join(OUT, 'h11_result.json'), 'w'), indent=2)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'corr'
    s = load(); i = meta(s)
    print(f'loaded {len(s)} legs across {len(i)} matches\n', flush=True)
    {'corr': corr, 'events': events}[cmd](s, i)
