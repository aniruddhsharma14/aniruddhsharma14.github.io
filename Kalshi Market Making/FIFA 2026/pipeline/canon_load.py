"""
The ONLY way downstream code should read this project's data.

    CANON_SUFFIX=_v2 python3 ...          # pick the tree; see canonical/README.md

    from canon_load import matches, legs, load_book, load_trades

    for m in matches():                   # the 27 depth_clean matches by default
        bk = load_book(m, 'KOR')          # one row per touch change, FULL ladder
        tr = load_trades(m, 'KOR')        # ts, price, size, aggressor

Nothing here re-derives the order book. If a number looks wrong, it is wrong in the
canonical layer and must be fixed in pipeline/, once, for every experiment at the
same time. That is the entire point of this file existing.
"""
import pandas as pd, numpy as np, glob, os, functools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(ROOT, 'canonical')
SPLITS = os.path.join(ROOT, 'splits')

# Reading a sealed match is blocked. Not because the file is precious -- because a
# sealed match that has been looked at is no longer a test set, and no file edit can
# undo that. To spend the sealed set deliberately:
#     import canon_load; canon_load.ALLOW_SEALED = True
# or set CANON_ALLOW_SEALED=1 in the environment. Both are recorded in the access log.
ALLOW_SEALED = os.environ.get('CANON_ALLOW_SEALED', '') == '1'

# CANON_SUFFIX selects the data tree, and it is REQUIRED for any data read:
#     _v2         the 27 depth_clean matches      <- the research universe
#     _priceonly  the 9 price_flow_only matches
#     _gaps       the 4 matches with dropped messages
#     orig        the original pre-repair build (books/, raw/) -- not for analysis
# There is deliberately no default. Unset used to mean the original build, whose
# sizes are wrong, so forgetting the variable silently produced bad numbers with
# only a warning. Now it fails before reading anything. Tree-independent helpers
# (windows, matches by quality tier, legs, teams, splits) work without it.
_RAW_SUF = os.environ.get('CANON_SUFFIX')
SUF = '' if _RAW_SUF in (None, 'orig') else _RAW_SUF


def _tree():
    if _RAW_SUF is None:
        raise RuntimeError(
            "CANON_SUFFIX is not set. Name the data tree explicitly: "
            "CANON_SUFFIX=_v2 (the 27 depth_clean matches), _priceonly, _gaps, "
            "or orig (the original pre-repair build, not for analysis).")
    return SUF

# Trees whose books are BYTE COPIES of the live tree (moved out by tier, not
# rebuilt). Their derived files -- validation.csv, consistency/ -- are the live
# ones, so falling back to those is correct. Any OTHER suffix must have its own
# derived files: silently pairing a rebuilt book with flags computed from the old
# one is exactly the kind of quiet mixing this module exists to prevent.
COPY_TREES = {'', '_priceonly', '_gaps'}


def _derived(name):
    """Path to a derived file or directory for the active tree: `name` + SUF,
    falling back to the live one only for trees that are copies of it."""
    _tree()
    base, ext = os.path.splitext(name)
    p = os.path.join(CANON, base + SUF + ext)
    if os.path.exists(p):
        return p
    if SUF in COPY_TREES:
        return os.path.join(CANON, name)
    raise FileNotFoundError(
        f"{p} does not exist. Tree '{SUF}' is a rebuild, not a copy of the live tree, "
        f"so its derived files must be regenerated (stage0_validate.py, "
        f"stage0_consistency.py with CANON_SUFFIX={SUF}). Refusing to fall back to the "
        f"live ones, which describe different books.")

# ---------------------------------------------------------------------------
# PER-LEG DATA QUALITY.  See H10 in hypotheses.md (2026-10-12) for the full
# diagnosis. Three tiers, recorded in canonical/leg_quality.csv:
#
#   depth_clean      81 legs / 27 matches  -- books seeded from a populated
#                    snapshot, zero sequence gaps. SIZES ARE USABLE. Verified
#                    externally on 26JUN14GERCUW against Predexon's independent
#                    reconstruction: 99.84-99.97% of ladder levels identical,
#                    18 levels lacked out of ~579,000. Only 2 of 27 matches have
#                    been checked externally; the rest are inferred from having
#                    the same two properties (snapshot applied, zero gaps).
#
#   gaps              3 legs / 1 match (26JUN12CANBIH) -- 266 dropped messages.
#                    Checked externally: 87.67-95.88%, flat across the window,
#                    which is what lost messages look like rather than a cold
#                    start. UNFIXABLE: the messages are gone. Deltas are also
#                    incomplete, so this match is unsafe for FLOW work too.
#
#   price_flow_only  36 legs / 12 matches -- behind the June 17-21 recording
#                    hole, so no clean seed exists. Books were built from deltas
#                    on an EMPTY start: sizes are LOWER BOUNDS, one-directional
#                    (never too large). Measured on 26JUN24RSAKOR: prices agree
#                    98%, sizes 90% at the touch, 23% beyond 31 ticks; our book
#                    a strict subset of theirs. PRICES, TRADES AND RAW DELTAS
#                    ARE SOUND -- flow decomposition is valid here. Absolute
#                    queue size and book depth are not.
#
# Trade prices/sizes and raw delta amounts are exchange-reported, never
# reconstructed, so they are unaffected in every tier except `gaps`.
SIZE_SUSPECT = ('bid_size', 'ask_size', 'microprice', 'bid_depth', 'ask_depth',
                'n_bid_levels', 'n_ask_levels', 'bid_sizes', 'ask_sizes')
_warned = set()


@functools.lru_cache(1)
def quality():
    """Per-leg tier. Missing file -> empty, and every leg is then treated as
    unknown, which warns. Silence by measuring, not by deleting the file."""
    fp = os.path.join(CANON, 'leg_quality.csv')
    if not os.path.exists(fp):
        return pd.DataFrame(columns=['match', 'leg', 'tier'])
    return pd.read_csv(fp)


def tier_of(match, leg):
    q = quality()
    if not len(q):
        return 'unknown'
    r = q[(q.match == match) & (q.leg == leg)]
    return str(r.tier.iloc[0]) if len(r) else 'unknown'


def _warn_sizes(match, leg, cols):
    hit = sorted(set(cols) & set(SIZE_SUSPECT))
    if not hit:
        return
    t = tier_of(match, leg)
    if t == 'depth_clean':
        return                      # measured good; nothing to warn about
    key = (match, leg, t)
    if key in _warned:
        return
    _warned.add(key)
    import warnings
    msg = {
        'gaps': (f"{match}-{leg} is tier 'gaps': 266 dropped messages in this "
                 f"match. Sizes drift and cannot be repaired. The raw deltas are "
                 f"incomplete too, so FLOW results are unsafe here, not just sizes."),
        'price_flow_only': (
            f"{match}-{leg} is tier 'price_flow_only': built from deltas with no "
            f"snapshot seed, so {', '.join(hit)} are LOWER BOUNDS (never too "
            f"large). Prices, trades and raw deltas are sound; absolute queue "
            f"size and depth are not."),
    }.get(t, f"{match}-{leg} has no quality record; treat sizes as unverified.")
    warnings.warn(f"canon_load: {msg} See H10 in hypotheses.md.", stacklevel=3)


@functools.lru_cache(1)
def splits():
    """The current (highest-numbered) split version. Older versions are kept."""
    vs = sorted(glob.glob(os.path.join(SPLITS, 'v*.csv')),
                key=lambda f: int(os.path.basename(f)[1:-4]))
    if not vs:
        return None
    return pd.read_csv(vs[-1]).set_index('match')


def split_of(match):
    sp = splits()
    if sp is None or match not in sp.index:
        return None
    return sp.loc[match, 'split']


def split_matches(which):
    """Match ids in a split of the CURRENT version. v2: 'cv' or 'sealed'
    (v1 used 'explore', 'validation', 'sealed'). For the recombination folds use
    the 'group' column of splits() -- each tournament group is one indivisible unit."""
    sp = splits()
    return sorted(sp.index[sp.split == which])


def _log_access(match, leg, kind):
    """Append every read to splits/access_log.csv, so 'is this match still clean?'
    is a lookup rather than a memory test -- and so a later re-split can be audited
    instead of guessed at."""
    try:
        os.makedirs(SPLITS, exist_ok=True)
        f = os.path.join(SPLITS, 'access_log.csv')
        new = not os.path.exists(f)
        import datetime as _dt, sys as _sys
        caller = '?'
        try:
            fr = _sys._getframe(3)
            caller = f"{os.path.basename(fr.f_code.co_filename)}:{fr.f_code.co_name}"
        except Exception:
            pass
        with open(f, 'a') as fh:
            if new:
                fh.write('ts_utc,match,leg,kind,split,caller\n')
            fh.write(f"{_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')},"
                     f"{match},{leg},{kind},{split_of(match)},{caller}\n")
    except Exception:
        pass        # logging must never break a read


@functools.lru_cache(1)
def validation():
    return pd.read_csv(_derived('validation.csv')).set_index('match')


@functools.lru_cache(1)
def windows():
    return pd.read_csv(os.path.join(CANON, 'windows.csv')).set_index('match')


QUALITY_TIERS = ('depth_clean', 'price_flow_only', 'gaps')


def matches(tier='depth_clean'):
    """Match ids in a data-quality tier.

    tier = 'depth_clean' (default), 'price_flow_only' or 'gaps' reads the PER-LEG
    tiers in canonical/leg_quality.csv, which are measured against an independent
    reconstruction (H10, hypotheses.md). A match qualifies only if EVERY leg is in
    that tier. Comma-separate to combine, e.g. 'depth_clean,price_flow_only'.

    tier = letters such as 'AB' or 'ABC' is the LEGACY selector over the A/B/C/F
    tiers in validation.csv for the active tree. It is kept so old scripts still
    run, but it is not the universe definition any more: on the live tree 'AB'
    returns 40 matches including 4 with dropped messages, because those tiers were
    decided before the cold-start defect was found.
    """
    names = [t.strip() for t in tier.split(',')]
    if all(t in QUALITY_TIERS for t in names):
        q = quality()
        if not len(q):
            raise FileNotFoundError('canonical/leg_quality.csv is missing')
        ok = q.groupby('match').tier.agg(lambda s: set(s) <= set(names))
        return sorted(ok.index[ok])
    if all(t in 'ABCF' for t in tier):
        v = validation()
        return sorted(v.index[v.tier.isin(list(tier))])
    raise ValueError(f"unknown tier {tier!r}; use one of {QUALITY_TIERS} or letters A-F")


def legs(match):
    """Leg codes for a match. From leg_quality.csv when the match is there (all 40
    tiered matches; needs no tree), else from the active tree's validation file."""
    q = quality()
    if len(q):
        r = q[q.match == match]
        if len(r):
            return sorted(r.leg)
    v = validation()
    return sorted(x.split(':')[0] for x in v.loc[match, 'legs'].split(';'))


def _guard(match):
    """Every data read passes here: the tree must be named, and a sealed match
    may not be read unless that is explicitly intended."""
    _tree()
    if split_of(match) == 'sealed' and not ALLOW_SEALED:
        raise PermissionError(
            f"{match} is in the SEALED set. Reading it spends the final test, and that "
            f"cannot be undone by editing the split file. If you mean to spend it, set "
            f"canon_load.ALLOW_SEALED = True (recorded in splits/access_log.csv).")


def _read(match, leg, kind, columns=None):
    if columns is not None and 'ts' not in columns:
        columns = ['ts'] + list(columns)   # ts is always needed to order the rows
    _guard(match)
    _log_access(match, leg, kind)
    # parts: <ticker>__<day>.<kind>.pNNN.parquet  (chunked writes keep memory flat)
    pat = os.path.join(CANON, 'books' + SUF, match, f'*-{leg}__*.{kind}.p*.parquet')
    fs = sorted(glob.glob(pat))
    if not fs:   # pre-chunking layout
        fs = sorted(glob.glob(os.path.join(CANON, 'books' + SUF, match,
                                           f'*-{leg}__*.{kind}.parquet')))
    if not fs:
        raise FileNotFoundError(pat)
    d = pd.concat([pd.read_parquet(f, columns=columns) for f in fs])
    return d.sort_values('ts').reset_index(drop=True)


def load_book(match, leg, columns=None, inplay_only=True):
    """Top-of-book series: one row per top-of-book CHANGE, with the FULL ladder
    as of that moment. inplay_only clips the 30-minute lead-in and the settlement
    tail, leaving kickoff -> final whistle.

    PRICES are sound. SIZES are lower bounds -- see SIZE_SUSPECT above."""
    d = _read(match, leg, 'book', columns)
    _warn_sizes(match, leg, d.columns)
    if inplay_only:
        w = windows().loc[match]
        d = d[(d.ts >= w.kickoff_ms) & (d.ts <= w.end_ms)].reset_index(drop=True)
    return d


def load_trades(match, leg, inplay_only=True):
    """aggressor is in YES terms: 'buy' lifted the ask, 'sell' hit the bid."""
    d = _read(match, leg, 'trades')
    if inplay_only:
        w = windows().loc[match]
        d = d[(d.ts >= w.kickoff_ms) & (d.ts <= w.end_ms)].reset_index(drop=True)
    return d


def teams(match):
    """'26JUN11KORCZE' -> ('KOR','CZE'). Needed for splitting BY TEAM: a team-specific
    effect leaks across a split made by match alone."""
    code = match[7:]
    return code[:3], code[3:6]


if __name__ == '__main__':
    ms = matches()
    print(f"{len(ms)} depth_clean matches (tree: books{SUF or ' (live)'})")
    m = ms[0]
    print(f"\nexample {m}: legs {legs(m)}")
    bk = load_book(m, legs(m)[0])
    print(f"  book {len(bk):,} rows x {len(bk.columns)} cols")
    print(f"  cols: {list(bk.columns)}")
    tr = load_trades(m, legs(m)[0])
    print(f"  trades {len(tr):,} rows, aggressor split: {tr.aggressor.value_counts().to_dict()}")


# ---------------------------------------------------------------- consistency
def load_consistency(match):
    """Per-state cross-leg consistency (see pipeline/stage0_consistency.py).
    Columns: ts, mid_sum, ask_sum, bid_sum, max_spread, max_age_ms, consistent."""
    return pd.read_parquet(os.path.join(_derived('consistency'), f'{match}.parquet'))


def load_book_clean(match, leg, columns=None, tol_ms=50):
    """load_book, restricted to states where the three legs price to 100 within
    tolerance. Use this for anything that depends on the touch being correct.

    Roughly 4% of states are internally inconsistent: one leg's touch is stale or
    drained while every leg looks healthy in isolation. Those states are invisible
    to per-leg checks and they are concentrated in the second half, exactly where
    the interesting price action is -- so they cannot be ignored by hoping they are
    rare or uniformly spread.
    """
    bk = load_book(match, leg, columns=columns)
    c = load_consistency(match)
    good = c.loc[c.consistent, 'ts'].values
    # a book row is kept when a consistent cross-leg state exists within tol_ms
    idx = np.searchsorted(good, bk.ts.values)
    prev_ok = np.where(idx > 0, bk.ts.values - good[np.clip(idx - 1, 0, len(good) - 1)], 1 << 60)
    next_ok = np.where(idx < len(good), good[np.clip(idx, 0, len(good) - 1)] - bk.ts.values, 1 << 60)
    return bk[np.minimum(prev_ok, next_ok) <= tol_ms].reset_index(drop=True)


def load_raw(match, leg):
    """Lossless source events for one leg: every orderbook delta and every
    snapshot level, in Kalshi's own string fields.

    Columns: ts, sequence, kind ('delta'|'snap'), side ('yes'|'no'),
             price_dollars, amount_fp
      - kind 'delta': amount_fp is a SIGNED change in resting size
      - kind 'snap' : amount_fp is an ABSOLUTE resting size

    The 'no' side is in NO prices. A NO level at q is a YES level at 100-q;
    use canon.DualBook if you want that inversion done for you.

    This is the layer to rebuild from. The book layer is derived from it.
    Guarded like the book layer: until 2026-10-16 it was not, so sealed raw deltas
    were readable without the flag.
    """
    _guard(match)
    _log_access(match, leg, 'raw')
    fs = sorted(glob.glob(os.path.join(CANON, 'raw' + SUF, match,
                                       f'*-{leg}__*.raw.p*.parquet')))
    if not fs:
        raise FileNotFoundError(f'no raw parts for {match} {leg}')
    d = pd.concat([pd.read_parquet(f) for f in fs])
    return d.sort_values(['ts', 'sequence']).reset_index(drop=True)
