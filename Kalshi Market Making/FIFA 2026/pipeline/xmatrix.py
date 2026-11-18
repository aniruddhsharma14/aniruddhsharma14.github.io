"""
H12 (hypotheses.md): do match-result prices co-move through team strength?

    python3 pipeline/xmatrix.py selftest   # known-answer test of the estimator
    python3 pipeline/xmatrix.py test       # registered primary test + secondaries
    python3 pipeline/xmatrix.py walk       # descriptive walk-forward matrices + Marchenko-Pastur

Series: one per (match, team) -- that team's win leg -- from canonical/ticker
(sealed groups excluded at extraction). Transform 2*asin(sqrt(p)). Live from the
first two-sided quote to the final whistle. No increment may span the recording
gap. Estimator: Hayashi-Yoshida on actual price-change times.
"""
from __future__ import annotations
import os, sys, json
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')
OUT = os.path.join(CANON, 'xmatrix')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

H = 3_600_000
GAP = (pd.Timestamp('2026-06-16 23:08', tz='UTC').value // 10**6,
       pd.Timestamp('2026-06-22 00:00', tz='UTC').value // 10**6)
RNG = np.random.default_rng(20261016)
MASK_MAINT = os.environ.get('MASK_MAINT', '') == '1'
MAINT = [(pd.Timestamp(f'{d} 07:00', tz='UTC').value // 10**6,
          pd.Timestamp(f'{d} 10:00', tz='UTC').value // 10**6)
         for d in ('2026-06-04', '2026-06-11', '2026-06-18', '2026-06-25')]


# ---------------------------------------------------------------- transforms
def arcsine(mid):
    p = np.clip(mid / 100.0, 0.0, 1.0)
    return 2 * np.arcsin(np.sqrt(p))


def logit(mid):
    p = np.clip(mid, 0.5, 99.5) / 100.0
    return np.log(p / (1 - p))


# ---------------------------------------------------------------- estimator
class Path:
    """Price-change path: increments dx over intervals (a, b]."""
    def __init__(self, t, x):
        keep = np.r_[True, np.diff(x) != 0]          # change points only
        t, x = t[keep], x[keep]
        a, b, dx = t[:-1], t[1:], np.diff(x)
        span_gap = (a < GAP[1]) & (b > GAP[0])       # never bridge the recording gap
        drop = span_gap
        if MASK_MAINT:
            # Kalshi's weekly maintenance pause: Thursday 03:00-05:00 ET (07:00-09:00 UTC
            # in June), plus the re-quote hour after it. Orders flagged
            # CancelOrderOnPause are cancelled at the start, so mids jump across many
            # markets at once and revert when quoting resumes -- spurious covariance
            # for every pair. Found AFTER H12's registered run; post-hoc correction only.
            for m0, m1 in MAINT:
                drop = drop | ((a < m1) & (b > m0))
        self.a, self.b, self.dx = a[~drop], b[~drop], dx[~drop]

    def window(self, lo, hi):
        m = (self.a >= lo) & (self.b <= hi)
        return self.a[m], self.b[m], self.dx[m]


def hy(px, py, lo, hi):
    """Hayashi-Yoshida correlation over [lo, hi]. Returns (rho, n_x, n_y)."""
    a, b, dx = px.window(lo, hi)
    c, d, dy = py.window(lo, hi)
    if len(dx) < 5 or len(dy) < 5:
        return np.nan, len(dx), len(dy)
    # y-intervals overlapping x-interval (a_i, b_i]: d_j > a_i and c_j < b_i
    j0 = np.searchsorted(d, a, side='right')
    j1 = np.searchsorted(c, b, side='left')
    cs = np.r_[0.0, np.cumsum(dy)]
    cov = float(np.sum(dx * (cs[j1] - cs[j0])))
    den = np.sqrt(np.sum(dx ** 2) * np.sum(dy ** 2))
    return (cov / den if den > 0 else np.nan), len(dx), len(dy)


# ---------------------------------------------------------------- self-test
def selftest():
    """Known answer: two Brownian motions over 10 days at rho = +0.6, 0, -0.5.
    X is observed every 10 s; Y about once an hour at random times, and held flat
    in between, as a stale quote is. HY must recover rho; a 1-minute grid Pearson
    should shrink towards zero (the Epps effect)."""
    T = 10 * 86_400
    n = T // 10                                   # latent 10 s steps
    for rho in (0.6, 0.0, -0.5):
        z1 = RNG.standard_normal(n); z2 = rho * z1 + np.sqrt(1 - rho ** 2) * RNG.standard_normal(n)
        tl = np.arange(n) * 10_000
        X, Y = np.cumsum(z1), np.cumsum(z2)
        tx = tl
        ty = np.sort(RNG.choice(tl, size=T // 3600, replace=False))
        ix, iy = np.searchsorted(tl, tx), np.searchsorted(tl, ty)
        r, nx, ny = hy(Path(tx, X[ix]), Path(ty, Y[iy]), tl[0], tl[-1])
        g = np.arange(0, tl[-1], 60_000)
        gx = X[np.searchsorted(tl, g)]; gy = Y[iy][np.clip(np.searchsorted(ty, g, 'right') - 1, 0, None)]
        pe = np.corrcoef(np.diff(gx), np.diff(gy))[0, 1]
        print(f'  true rho {rho:+.2f}:  HY {r:+.3f}  (x {nx:,} / y {ny:,} increments)   '
              f'1-min grid Pearson {pe:+.3f}')


# ---------------------------------------------------------------- data
def build(transform=arcsine):
    import xmatch as X
    s = X.load(); info = X.meta(s)
    from ticker_extract import groups_of_fixtures
    grp = groups_of_fixtures()
    legs = {}
    for m, v in info.items():
        for team in (m[7:10], m[10:13]):
            if (m, team) not in s:
                continue
            t, mid = s[(m, team)]
            live = (t >= v['first']) & (t <= v['end'])
            t, mid = t[live], mid[live].astype('float64')
            if len(t) < 10:
                continue
            legs[(m, team)] = dict(path=Path(t, transform(mid)), lo=int(t[0]), hi=int(v['end']),
                                   kickoff=v['kickoff'], group=grp[m],
                                   opp=m[10:13] if team == m[7:10] else m[7:10])
    return legs


def pair_class(k1, k2, legs):
    (m1, x), (m2, y) = k1, k2
    if m1 == m2:
        return 'same_match'
    if x == y:
        return 'same_team'
    if legs[k1]['group'] != legs[k2]['group']:
        return 'cross_group'
    # the two teams meet in one of the two matches
    if legs[k1]['opp'] == y or legs[k2]['opp'] == x:
        return 'future_opponent'
    return 'same_group_other'


def all_pairs(legs, pre_kickoff_only=False):
    ks = sorted(legs)
    rows = []
    for i, k1 in enumerate(ks):
        for k2 in ks[i + 1:]:
            cl = pair_class(k1, k2, legs)
            if cl == 'same_match':
                continue
            L1, L2 = legs[k1], legs[k2]
            lo, hi = max(L1['lo'], L2['lo']), min(L1['hi'], L2['hi'])
            if pre_kickoff_only:
                ks_ = [x for x in (L1['kickoff'], L2['kickoff']) if x]
                if ks_:
                    hi = min(hi, min(ks_))
            if hi - lo < 6 * H:
                continue
            r, n1, n2 = hy(L1['path'], L2['path'], lo, hi)
            rows.append(dict(a=f'{k1[0]}:{k1[1]}', b=f'{k2[0]}:{k2[1]}', cls=cl,
                             group=L1['group'] if L1['group'] == L2['group'] else None,
                             rho=r, n_a=n1, n_b=n2, hours=(hi - lo) / H))
    return pd.DataFrame(rows)


def sign_tests(p):
    out = {}
    for cl, want in (('same_team', +1), ('future_opponent', -1)):
        g = p[p.cls == cl].dropna(subset=['rho']).groupby('group').rho.mean()
        hits = int((np.sign(g) == want).sum())
        out[cl] = dict(groups=len(g), hits=hits, per_group=g.round(3).to_dict())
    return out


def test():
    global OUT
    if MASK_MAINT:
        OUT = os.path.join(CANON, 'xmatrix_maintmask')
        print('*** POST-HOC: maintenance windows masked. Registered verdict is NOT changed by this run. ***\n')
    os.makedirs(OUT, exist_ok=True)
    legs = build(arcsine)
    p = all_pairs(legs)
    p.to_parquet(os.path.join(OUT, 'h12_pairs.parquet'), index=False)
    print(f'{len(legs)} legs, {len(p):,} scored pairs\n')
    print(p.dropna(subset=['rho']).groupby('cls').rho
          .agg(pairs='size', mean='mean', median='median').round(4).to_string())

    st = sign_tests(p)
    n1, n2 = st['same_team']['hits'], st['future_opponent']['hits']
    print(f"\n=== P1  same_team mean > 0      (predicted >= 8 of 10 groups): {n1} of {st['same_team']['groups']}")
    print('   ', st['same_team']['per_group'])
    print(f"=== P2  future_opponent mean < 0 (predicted >= 8 of 10 groups): {n2} of {st['future_opponent']['groups']}")
    print('   ', st['future_opponent']['per_group'])

    # P3 permutation, same_team vs cross_group
    q = p.dropna(subset=['rho']); q = q[q.cls.isin(['same_team', 'cross_group'])]
    obs = q[q.cls == 'same_team'].rho.mean() - q[q.cls == 'cross_group'].rho.mean()
    lab = (q.cls == 'same_team').to_numpy(); r = q.rho.to_numpy(); k = lab.sum()
    perm = np.array([r[idx[:k]].mean() - r[idx[k:]].mean()
                     for idx in (RNG.permutation(len(r)) for _ in range(5000))])
    p3 = float((perm >= obs).mean())
    print(f'=== P3  same_team - cross_group = {obs:+.4f}, permutation p = {p3:.4f}  (one-sided, 5%)')

    # P4 clustering vs groups
    ari, p4 = cluster_vs_groups(legs, p)
    print(f'=== P4  clustering vs true groups: ARI {ari:+.3f}, permutation p = {p4:.4f}')

    verdict = ('SUPPORTED' if n1 >= 8 and n2 >= 8 else
               'PARTIAL: co-movement, not strength updating (P2 failed)' if n1 >= 8 else
               'REJECTED: P1 failed (and P2 ' + ('passed' if n2 >= 8 else 'failed') + ')')
    print(f'\n=== H12 VERDICT: {verdict} ===')
    json.dump(dict(p1=n1, p2=n2, p3_p=p3, p4_ari=ari, p4_p=p4, verdict=verdict,
                   per_group=st), open(os.path.join(OUT, 'h12_result.json'), 'w'), indent=2, default=float)

    # logged sensitivity runs -- cannot rescue the primary
    print('\n--- sensitivity runs (logged trials; cannot rescue the primary) ---')
    for name, kw, tf in (('logit', {}, logit), ('pre-kickoff only', dict(pre_kickoff_only=True), arcsine)):
        lg = build(tf) if tf is not arcsine else legs
        ps = all_pairs(lg, **kw); s2 = sign_tests(ps)
        print(f"  {name:<17} P1 {s2['same_team']['hits']}/{s2['same_team']['groups']}   "
              f"P2 {s2['future_opponent']['hits']}/{s2['future_opponent']['groups']}")
        ps.to_parquet(os.path.join(OUT, f"h12_pairs_{name.split()[0]}.parquet"), index=False)


def cluster_vs_groups(legs, p):
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import squareform
    from sklearn.metrics import adjusted_rand_score
    ks = sorted(legs); ix = {f'{m}:{t}': i for i, (m, t) in enumerate(ks)}
    n = len(ks); C = np.zeros((n, n)); np.fill_diagonal(C, 1.0)
    for r in p.dropna(subset=['rho']).itertuples():
        i, j = ix[r.a], ix[r.b]; C[i, j] = C[j, i] = np.clip(r.rho, -1, 1)
    D = np.sqrt(0.5 * (1 - C)); np.fill_diagonal(D, 0)
    lab = fcluster(linkage(squareform(D, checks=False), 'average'), 10, 'maxclust')
    truth = np.array([legs[k]['group'] for k in ks])
    ari = adjusted_rand_score(truth, lab)
    perm = np.array([adjusted_rand_score(RNG.permutation(truth), lab) for _ in range(5000)])
    return float(ari), float((perm >= ari).mean())


# ---------------------------------------------------------------- descriptive
def walk():
    os.makedirs(OUT, exist_ok=True)
    legs = build(arcsine)
    days = pd.date_range('2026-06-02', '2026-06-28', freq='D', tz='UTC')
    rows = []
    ks = sorted(legs)
    for d0 in days:
        lo = d0.value // 10**6; hi = lo + 3 * 86_400_000
        live = [k for k in ks if legs[k]['lo'] < hi and legs[k]['hi'] > lo]
        for i, k1 in enumerate(live):
            for k2 in live[i + 1:]:
                cl = pair_class(k1, k2, legs)
                if cl == 'same_match':
                    continue
                w0, w1 = max(lo, legs[k1]['lo'], legs[k2]['lo']), min(hi, legs[k1]['hi'], legs[k2]['hi'])
                if w1 - w0 < 6 * H:
                    continue
                r, n1, n2 = hy(legs[k1]['path'], legs[k2]['path'], w0, w1)
                if np.isfinite(r):
                    rows.append(dict(window_start=d0.strftime('%Y-%m-%d'), a=f'{k1[0]}:{k1[1]}',
                                     b=f'{k2[0]}:{k2[1]}', cls=cl, rho=r))
    w = pd.DataFrame(rows)
    w.to_parquet(os.path.join(OUT, 'walk_forward.parquet'), index=False)
    print('walk-forward (3-day windows): pairs with an estimate, and mean rho by class')
    print(w.pivot_table(index='window_start', columns='cls', values='rho', aggfunc='mean').round(3).to_string())
    marchenko_pastur(legs)


def marchenko_pastur(legs):
    lo = pd.Timestamp('2026-06-11', tz='UTC').value // 10**6
    hi = pd.Timestamp('2026-06-16 23:00', tz='UTC').value // 10**6
    g = np.arange(lo, hi + 1, H)
    cols = []
    import xmatch as X
    s = X.load()
    for k, L in legs.items():
        if L['lo'] <= lo and L['hi'] >= hi:
            t, mid = s[k]
            v = arcsine(X.mid_at((t, mid), g))
            dv = np.diff(v)
            if np.isfinite(dv).all() and dv.std() > 0:
                cols.append(dv)
    M = np.array(cols)
    N, T = M.shape
    ev = np.sort(np.linalg.eigvalsh(np.corrcoef(M)))[::-1]
    lam = (1 + np.sqrt(N / T)) ** 2
    print(f'\nMarchenko-Pastur, June 11-16 hourly: N={N} series, T={T} hours, q={N/T:.2f}, '
          f'noise edge lambda+ = {lam:.2f}')
    print(f'  eigenvalues above the edge: {int((ev > lam).sum())}   top five: {np.round(ev[:5], 2).tolist()}')


if __name__ == '__main__':
    {'selftest': selftest, 'test': test, 'walk': walk}[sys.argv[1] if len(sys.argv) > 1 else 'selftest']()
