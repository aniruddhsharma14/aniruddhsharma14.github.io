"""
THE foundational experiment: do microstructure features predict short-horizon price
change OUT-OF-SAMPLE, beyond what the current price already reflects?

Honest design:
  - Target (label): change in MICROPRICE over the next HORIZON seconds. Microprice (not
    mid) because mid is discrete/sparse; microprice is what we'd quote around.
  - Features: microstructure only, all STRICTLY BACKWARD-LOOKING (no leakage).
  - Validation: MATCH-OUT-OF-SAMPLE (train on one match, test on a DIFFERENT match).
    Within-match testing would inflate the score via autocorrelation -- forbidden.
  - Benchmark: the random-walk null. EMH says microprice is a martingale, so the best
    naive forecast of price change is ZERO. We only have edge if we beat "predict 0"
    out-of-sample. Metric: OOS R^2 vs zero, sign accuracy, corr(pred, realized).

Run:
    python3 drift_experiment.py
(edit TRAIN/TEST at the bottom to point at your tapes)
"""
from __future__ import annotations
import numpy as np
import backtest                       # reuse validated DualBook + iter_tape
from backtest import DualBook

SAMPLE_MS = 1000          # sample the state once per second
HORIZON_S = 5             # predict microprice change this many seconds ahead


def _topk_vol(book_side, prices_sorted, k):
    return sum(book_side[p] for p in prices_sorted[:k])


def microprice(book, levels=3):
    bb, ba = book.best_bid(), book.best_ask()
    if bb is None or ba is None:
        return None
    tb = sorted((p for p in book.bids if book.bids[p] >= 0.5), reverse=True)[:levels]
    ta = sorted(p for p in book.asks if book.asks[p] >= 0.5)[:levels]
    bv = sum(book.bids[p] for p in tb); av = sum(book.asks[p] for p in ta)
    if bv + av <= 0:
        return (bb + ba) / 2.0
    return (bb * av + ba * bv) / (bv + av)


def build_series(path, ticker, seed_from_deltas=False, warmup_ms=300000,
                 t0_ms=None, t1_ms=None):
    """Walk the tape; every SAMPLE_MS emit (t_ms, microprice, feature_dict),
    all features computed from STRICTLY PAST data.
    t0_ms/t1_ms optionally clip to a match window (book still BUILDS from before t0,
    but samples are only emitted within [t0, t1]) -- so the from-empty book can warm up
    on pre-window deltas while the analysis stays inside the match."""
    book = DualBook()
    if seed_from_deltas:
        book.has_book = True
    if isinstance(path, str):
        path = [path]

    micro_hist = []          # (t_ms, microprice) recent, for realized vol
    trade_hist = []          # (t_ms, signed_size) recent, for trade-sign imbalance
    rows = []                # (t_ms, microprice, features)
    first_ts = None
    next_sample = None
    _started = [False]        # becomes True when the sampling grid is snapped to window-open

    def evs():
        for p in path:
            for ev in backtest.iter_tape(p, ticker):
                yield ev

    for typ, msg, seq, ts in evs():
        if ts is None:
            continue
        if t1_ms is not None and ts > t1_ms:
            break                                  # past the window; stop
        if first_ts is None:
            first_ts = ts; next_sample = ts
        if typ == "orderbook_snapshot":
            book.snapshot(msg)
        elif typ == "orderbook_delta":
            if not book.has_book:
                continue
            book.delta(msg, seq)
        elif typ == "trade":
            if not book.has_book:
                continue
            price = backtest._c(msg["yes_price_dollars"])
            size = float(msg.get("count_fp", 0))
            # bid == yes: taker bought YES -> aggressive buy (+1). See Kalshi API docs.
            sign = +1.0 if msg.get("taker_book_side") == "bid" else -1.0
            trade_hist.append((ts, sign * size))

        if not book.has_book:
            continue
        mp = microprice(book)
        if mp is not None:
            micro_hist.append((ts, mp))
        # prune buffers to last 60s
        cutoff = ts - 60000
        while micro_hist and micro_hist[0][0] < cutoff:
            micro_hist.pop(0)
        while trade_hist and trade_hist[0][0] < cutoff:
            trade_hist.pop(0)

        # sample on the time grid (skip during warmup for seed_from_deltas; and only
        # inside the [t0,t1] match window if one was given)
        in_window = (t0_ms is None or ts >= t0_ms)
        warmed = (ts - first_ts) >= (warmup_ms if seed_from_deltas else 0)
        if mp is not None and in_window and warmed:
            if not _started[0]:
                next_sample = ts          # snap grid to window-open: clean 1/sec from here
                _started[0] = True
        if (mp is not None and ts >= next_sample and in_window and warmed and _started[0]):
            bb, ba = book.best_bid(), book.best_ask()
            tb = sorted((p for p in book.bids if book.bids[p] >= 0.5), reverse=True)
            ta = sorted(p for p in book.asks if book.asks[p] >= 0.5)
            if bb is not None and ba is not None and tb and ta:
                b1 = book.bids[tb[0]]; a1 = book.asks[ta[0]]
                b3 = _topk_vol(book.bids, tb, 3); a3 = _topk_vol(book.asks, ta, 3)
                imb1 = (b1 - a1) / (b1 + a1) if (b1 + a1) > 0 else 0.0
                imb3 = (b3 - a3) / (b3 + a3) if (b3 + a3) > 0 else 0.0
                mid = (bb + ba) / 2.0
                # trade-sign imbalance over 5s and 30s (backward)
                def tsi(window_ms):
                    s = sum(sz for tt, sz in trade_hist if tt >= ts - window_ms)
                    n = sum(abs(sz) for tt, sz in trade_hist if tt >= ts - window_ms)
                    return s / n if n > 0 else 0.0
                # realized vol of microprice over 10s/30s (backward)
                def rv(window_ms):
                    pts = [mpv for tt, mpv in micro_hist if tt >= ts - window_ms]
                    if len(pts) < 3:
                        return 0.0
                    d = np.diff(pts)
                    return float(np.std(d))
                feats = {
                    "imb1": imb1, "imb3": imb3,
                    "micro_minus_mid": mp - mid,
                    "tsi_5s": tsi(5000), "tsi_30s": tsi(30000),
                    "rv_10s": rv(10000), "rv_30s": rv(30000),
                    "spread": ba - bb,
                    "depth_near": b3 + a3,
                }
                rows.append((ts, mp, feats))
            next_sample += SAMPLE_MS

    return rows


def make_xy(rows, feat_names):
    """Attach the HORIZON-ahead microprice-change label (no look-ahead: label uses
    only future points; features use only past)."""
    ts = np.array([r[0] for r in rows])
    mp = np.array([r[1] for r in rows])
    X = np.array([[r[2][f] for f in feat_names] for r in rows])
    y = np.full(len(rows), np.nan)
    for i in range(len(rows)):
        t_future = ts[i] + HORIZON_S * 1000
        j = np.searchsorted(ts, t_future)
        if j < len(ts):
            y[i] = mp[j] - mp[i]              # realized microprice change over horizon
    ok = ~np.isnan(y)
    return X[ok], y[ok]


def run(train, test, seed_train=False, seed_test=False):
    feat_names = ["imb1", "imb3", "micro_minus_mid", "tsi_5s", "tsi_30s",
                  "rv_10s", "rv_30s", "spread", "depth_near"]
    print("building train series..."); rtr = build_series(*train, seed_from_deltas=seed_train)
    print("building test series ..."); rte = build_series(*test, seed_from_deltas=seed_test)
    Xtr, ytr = make_xy(rtr, feat_names)
    Xte, yte = make_xy(rte, feat_names)
    print(f"train samples: {len(ytr)}   test samples: {len(yte)}")

    # standardize with TRAIN stats only (no leakage)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Xtr_s = (Xtr - mu) / sd; Xte_s = (Xte - mu) / sd

    # ridge regression (closed form), light regularization
    lam = 10.0
    A = Xtr_s.T @ Xtr_s + lam * np.eye(Xtr_s.shape[1])
    w = np.linalg.solve(A, Xtr_s.T @ ytr)
    b = ytr.mean() - (Xtr_s.mean(0) @ w)

    pred = Xte_s @ w + b
    # metric vs the ZERO-prediction null (EMH martingale benchmark)
    ss_res = np.sum((yte - pred) ** 2)
    ss_zero = np.sum(yte ** 2)
    r2_vs_zero = 1.0 - ss_res / ss_zero
    sign_acc = np.mean(np.sign(pred) == np.sign(yte))
    corr = np.corrcoef(pred, yte)[0, 1] if np.std(pred) > 0 else 0.0

    print("\n=== OUT-OF-SAMPLE RESULTS (test match) ===")
    print(f"  R^2 vs predicting-zero : {r2_vs_zero:+.4f}   (>0 means real edge)")
    print(f"  sign accuracy          : {sign_acc:.3f}      (0.5 = coin flip)")
    print(f"  corr(pred, realized)   : {corr:+.3f}")
    print("\n  feature weights (standardized):")
    for f, wi in sorted(zip(feat_names, w), key=lambda kv: -abs(kv[1])):
        print(f"    {f:18s} {wi:+.4f}")
    print("\nInterpretation: R^2<=0 -> microstructure does NOT predict price beyond the")
    print("current price (efficient at this horizon). R^2>0 OOS -> real, tradeable structure.")


if __name__ == "__main__":
    # EDIT THESE to your tapes. Train on one match, test on the OTHER (out-of-sample).
    TRAIN = ("/Users/aniruddhsharma/Desktop/worldcup/recordings/kalshi-2026-06-12-MEXRSA-MEX.jsonl",
             "KXWCGAME-26JUN11MEXRSA-MEX")
    TEST  = ("/Users/aniruddhsharma/Desktop/worldcup/recordings/KORCZE-all-jun11.jsonl",
             "KXWCGAME-26JUN11KORCZE-KOR")
    run(TRAIN, TEST, seed_train=False, seed_test=True)   # KOR-CZE needs seed_from_deltas
