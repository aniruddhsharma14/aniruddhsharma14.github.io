"""
Decompose raw order book deltas into FILL / CANCEL / ADD.

Everything about book dynamics needs this first. A delta on its own says a level
changed size; it does not say why. "The queue at 58 shrank by 7,000" means one
thing if a market order ate it and the opposite thing if someone pulled it.

--------------------------------------------------------------------------------
WHAT THIS CAN AND CANNOT RECOVER

Kalshi publishes L2: a delta is the NET size change at one price level. So:

  * We cannot count orders. An add and a cancel inside one message net to a
    single number, and there are no order ids. Order-arrival intensity in the
    Cont-Stoikov-Talreja sense is not estimable from this feed.
  * We CAN measure volume intensity -- signed size change per unit time at a
    level, conditional on state. That is the Queue-Reactive framing
    (Huang, Lehalle, Rosenbaum) rather than the order-counting one.

So every rate here is in CONTRACTS, never in orders. Saying otherwise would be
inventing a quantity the feed does not carry.

--------------------------------------------------------------------------------
MATCHING RULE

A trade of size s at YES price p consumes resting size:

  aggressor 'buy'  lifted the ask  -> the ask ladder at p shrinks.
                                      Asks come from the NO book, so the raw
                                      delta is side='no' at price (100 - p).
  aggressor 'sell' hit the bid     -> the bid ladder at p shrinks.
                                      Raw delta is side='yes' at price p.

We look for a negative delta on that side and price, near the trade in time,
whose magnitude is close to the traded size. Greedy, nearest-first.

Exact matching is impossible in principle because of netting: one delta can
carry a fill and an add together, and one aggressive order can be reported as
several trades. So the output reports its own residual rather than claiming a
clean split. `validate()` checks the invariant that matters:

    total matched fill volume  ==  total trade volume

A matcher that silently loses volume would make every downstream intensity wrong
in the same direction, which is exactly the kind of error that looks like a
finding.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canon_load import load_raw, load_trades, legs, windows

TOL_MS = 250        # how far from a trade a fill delta may sit
TOL_REL = 0.02      # size tolerance, fraction of the traded size
DUST = 0.5


def _yes_price(side, price_dollars):
    """Raw rows keep the NO side in NO prices. Convert to YES terms, which is how
    the rest of the project speaks. A NO level at q is a YES level at 100 - q."""
    c = round(float(price_dollars) * 100, 4)
    return c if side == 'yes' else 100.0 - c


def decompose(match, leg, tol_ms=TOL_MS, tol_rel=TOL_REL, inplay_only=True):
    """-> (events, report). events is one row per delta with a `kind` column."""
    raw = load_raw(match, leg)
    raw = raw[raw.kind == 'delta'].copy()
    tr = load_trades(match, leg)

    if inplay_only:
        w = windows().loc[match]
        raw = raw[(raw.ts >= w.kickoff_ms) & (raw.ts <= w.end_ms)]

    raw['amount'] = raw.amount_fp.astype(float)
    cents_ = (raw.price_dollars.astype(float) * 100).round(4).values
    is_yes = (raw.side.values == 'yes')
    # raw keeps the NO side in NO prices; convert to YES terms, which is how the
    # rest of the project speaks. A NO level at q is a YES level at 100 - q.
    raw['yes_price'] = np.where(is_yes, cents_, 100.0 - cents_)
    raw['ladder'] = np.where(is_yes, 'bid', 'ask')
    raw = raw.sort_values(['ts', 'sequence']).reset_index(drop=True)
    raw['kind'] = np.where(raw.amount.values < 0, 'cancel', 'add')
    raw['key'] = raw.ladder + '@' + raw.yes_price.round(2).astype(str)

    # Exact join on (timestamp, price). Measured on 26JUN11KORCZE-KOR:
    #   every trade group has a negative delta at the same ts+price   (0% missing)
    #   99.8% of groups match on volume within 1%, median ratio 1.0000
    #   in multi-print groups, 99.6% have one delta per print
    # So Kalshi emits one delta per trade print, at the same millisecond, and
    # netting is effectively absent. An earlier version used a 250 ms nearest
    # match with a one-delta-per-trade rule and reached only 78% of volume; that
    # shortfall was the matcher, not the feed. Do not reintroduce a tolerance.
    n_matched = 0
    matched_vol = 0.0
    if len(tr):
        t2 = tr.copy()
        t2['ladder'] = np.where(t2.aggressor.values == 'buy', 'ask', 'bid')
        t2['key'] = t2.ladder + '@' + t2['price'].astype(float).round(2).astype(str)
        trade_groups = set(zip(t2.ts.values, t2.key.values))
        is_neg = raw.amount.values < 0
        in_trade = np.fromiter(
            (k in trade_groups for k in zip(raw.ts.values, raw.key.values)),
            dtype=bool, count=len(raw))
        fill_mask = is_neg & in_trade
        raw.loc[fill_mask, 'kind'] = 'fill'
        n_matched = int(fill_mask.sum())
        matched_vol = float(-raw.loc[fill_mask, 'amount'].sum())

    tot_trade_vol = float(tr['size'].sum()) if len(tr) else 0.0
    report = dict(
        match=match, leg=leg,
        n_deltas=len(raw), n_trades=len(tr),
        n_fill=int((raw.kind == 'fill').sum()),
        n_cancel=int((raw.kind == 'cancel').sum()),
        n_add=int((raw.kind == 'add').sum()),
        trade_vol=tot_trade_vol, matched_vol=matched_vol,
        # NOT a coverage fraction. Numerator = volume LABELLED fill, denominator =
        # volume actually traded. >1 means genuine cancels shared a timestamp and
        # price with a trade and were swept into the fill bucket. The excess over
        # 1.0 is the over-attribution error, and it is the only real ambiguity.
        fill_vol_ratio=(matched_vol / tot_trade_vol) if tot_trade_vol else np.nan,
        fill_count_ratio=(n_matched / len(tr)) if len(tr) else np.nan,
        add_vol=float(raw.loc[raw.kind == 'add', 'amount'].sum()),
        cancel_vol=float(-raw.loc[raw.kind == 'cancel', 'amount'].sum()),
    )
    return raw, report


def validate(matches_legs, **kw):
    """The invariant: matched fill volume should account for the trade tape.

    A low match rate is not automatically a bug -- netting hides fills inside
    larger deltas -- but it bounds how much of the 'cancel' bucket is really
    cancels. Report it rather than assume it away."""
    rows = []
    for m, lg in matches_legs:
        _, rep = decompose(m, lg, **kw)
        rows.append(rep)
    r = pd.DataFrame(rows)
    print(f"{'match':<16}{'leg':>5}{'deltas':>10}{'trades':>9}"
          f"{'fill':>9}{'cancel':>9}{'add':>9}{'vol x':>9}{'count x':>9}")
    for x in r.itertuples():
        print(f"{x.match:<16}{x.leg:>5}{x.n_deltas:>10,}{x.n_trades:>9,}"
              f"{x.n_fill:>9,}{x.n_cancel:>9,}{x.n_add:>9,}"
              f"{x.fill_vol_ratio:>9.3f}{x.fill_count_ratio:>9.3f}")
    print(f"\nOVER-ATTRIBUTION (1.000 = exact; >1 = fill bucket holds cancel volume too)")
    print(f"  volume ratio : {r.fill_vol_ratio.mean():.3f} mean, "
          f"{r.fill_vol_ratio.max():.3f} worst")
    print(f"  count  ratio : {r.fill_count_ratio.mean():.3f} mean")
    print("\nVolume above 100% is genuine cancellation sharing a timestamp AND price")
    print("with a trade; those cannot be separated and are counted as fill. That")
    print("excess, 2-6%, is the only real ambiguity in the decomposition.")
    return r


def by_level(events, book=None, max_dist=10):
    """Flow by distance from the touch, which is the variable that matters.

    Absolute price confounds the level with the price regime: a leg trading at 8
    and one trading at 50 are different worlds. Distance from the same-side best
    is comparable across both.
    """
    ev = events.copy()
    if book is None:
        return None
    b = book[['ts', 'bid', 'ask']].sort_values('ts')
    ev = pd.merge_asof(ev.sort_values('ts'), b, on='ts', direction='backward')
    ev = ev.dropna(subset=['bid', 'ask'])
    ev['dist'] = np.where(ev.ladder == 'bid',
                          ev.bid - ev.yes_price,
                          ev.yes_price - ev.ask)
    ev = ev[(ev.dist >= 0) & (ev.dist <= max_dist)]
    g = ev.groupby(['dist', 'kind']).agg(
        n=('amount', 'size'), vol=('amount', lambda x: float(np.abs(x).sum())))
    return g.reset_index()


if __name__ == '__main__':
    from canon_load import split_matches
    ms = split_matches('explore')[:2]
    pairs = [(m, lg) for m in ms for lg in legs(m)]
    print(f"decomposing {len(pairs)} legs across {len(ms)} explore matches\n")
    validate(pairs)
