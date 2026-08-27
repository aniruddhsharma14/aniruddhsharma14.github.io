# Kalshi Prediction Market Microstructure

Order book reconstruction, optimal market making, and an empirical study of why passive
liquidity provision is hard on binary sports event contracts.

Independent research on ~40 FIFA 2026 World Cup matches of tick-level Kalshi data. (Still ongoing)

---

## What this is

Kalshi runs a central limit order book for binary event contracts that settle at $0 or $1.
Each football match is listed as three mutually exclusive contracts — home win, away win,
draw — so their prices must sum to 100 cents.

This repository contains:

1. **Data capture and reconstruction** — a WebSocket recorder and a book reconstructor that
   handles Kalshi's dual YES/NO book structure, validated against ground truth.
2. **An optimal market maker** — Cartea–Jaimungal–Penalva quoting adapted to binary
   settlement, with parameter calibration and a queue-aware backtester.
3. **An empirical microstructure study** — measurements of spread, queue depth, price
   dynamics and pre-jump conditions that explain why the strategy in (2) does not work
   at small size on this venue.

The third part is the substance. The headline result is negative and well-supported.

---

## The one mechanic that matters

Kalshi maintains **two books per contract**: a YES book of bids to buy YES, and a NO book of
bids to buy NO. A NO bid at price `q` is economically a YES **ask** at `100 - q`.

So the YES market's ask side must be derived by flipping the NO book:

```
best_ask = 100 - max(NO bids)
```

Nearly every reconstruction bug in this project traced back to this. `inspect_book.py`
prints the two books side by side so the conversion is visible.

A second subtlety: `taker_book_side` names the **outcome the taker bought**, not the resting
side they hit (`bid ≡ yes`, `ask ≡ no`, per the Kalshi API docs). A taker who bought YES is
an aggressive *buyer*; one who bought NO is economically an aggressive *seller* of YES.
Getting this backwards inverts every order-flow feature.

---

## Reconstruction validation

Two independent checks, both passed:

- **Leg-sum identity.** The three contracts of a match are mutually exclusive and exhaustive,
  so their mid prices must sum to ~100. Across 309,257 reconstructed states of one match the
  sum had mean **99.99** (IQR 99.50–100.50). The half-tick excess over 100 is the bid-ask
  overhang, as expected.
- **Against the exchange's own chart.** Reconstructed price paths match Kalshi's published
  probability series, including endpoints at settlement.

When no snapshot precedes the analysis window, the book is seeded from deltas alone: start
empty and let deltas paint in levels as they are touched. Near-touch prices converge within
minutes; deep untouched levels remain invisible. Prices are reliable under this mode, depth
is a lower bound.

---

## Empirical findings

Measured on reconstructed World Cup order books. Sample sizes are stated because several of
these rest on a single match and should be treated as provisional.

**Spreads are pinned at the minimum.** Of 161,613 book states in one match, **95.3%** had a
one-tick spread; 3.4% were two ticks. There is almost never room to improve the quote.

**Touch queues are enormous.** Median resting size at the best bid and ask ran to ~59,000
contracts, with individual levels holding 300,000–1,500,000. A small order joins the back of
a queue that a whole match's volume will not clear.

**The book is a barbell.** Dense within two or three ticks of the mid, near-empty in the
middle of the range (single-contract levels), with large walls at the extremes (1¢ and 99¢).
Price therefore either steps one tick or gaps several — there is nothing in between to
absorb a sweep.

**Price moves are walks, but too fast to react to.** 97.8% of touch changes are a single
tick. But the median gap between changes is **8 ms**, so only 19% of moves are reactable at a
50 ms reaction time (41% at 10 ms, 74% at 1 ms).

**Short-horizon moves trend rather than revert.** Mean retracement after a 1-tick move over
the following 5 s is **−0.15** — the price continues rather than coming back. Only 12% of
moves gave back more than half within 5 s. Passive two-sided quoting therefore fills
systematically on the losing side.

**Dislocations are effectively instantaneous.** A goal repriced the market from a 2-tick to a
15-tick spread in **25 ms**, with touch depth collapsing from 2,846 contracts to 24. No
cancellation policy operates on that timescale.

**Depth collapse is not a usable precursor.** Near-touch depth drops two orders of magnitude
dozens of times per match, mostly without a price move following. It fires far too often to
gate quoting on.

**Stable-book periods have less flow, not more.** Classifying the match into deep-and-steady
versus flickering, stable periods (37% of the window) showed roughly *half* the trade count
and volume. Calm conditions are calm because nothing is happening.

### Why the strategy fails

These compose into a single mechanism. In calm conditions a small order cannot fill, because
it sits behind a six-figure queue. It fills only when a sweep clears that queue — which is
precisely when the price is moving against it. **Flow and adverse selection are coupled: there
is no regime offering one without the other.**

---

## Repository layout

```
capture/
  recorder.py            always-on WebSocket recorder, periodic re-snapshotting
reconstruction/
  backtest.py            DualBook reconstruction + tape replay (also the backtest engine)
  inspect_book.py        step through raw messages; shows the YES/NO mechanic explicitly
  book_ladder.py         full price ladder at depth N, depth-of-market printout
strategy/
  cjp_market_maker.py    CJP/Avellaneda-Stoikov engine (backward HJB on the inventory grid)
  calibrate.py           arrival rate, fill decay and volatility estimation from trades
  shadow_trader.py       queue-aware fill model with maker fees
  microprice_mm.py       microprice-centred maker with an adaptive reprice threshold
analysis/
  walk_gap.py            walk-vs-gap classification and retracement
  precursor.py           conditions before large moves, against a calm baseline
  depth_map.py           full-book depth heatmap over time
  stability.py           stable/unstable regime classification and flow comparison
  book_alpha.py          does book state predict forward price? (leakage-controlled)
notebooks/
  book_explorer.ipynb    reconstruct and inspect a match
  microprice_compare.ipynb  microprice variants against the mid
```

---

## Method notes

Several results changed materially once measurement bugs were found. Recording them here
because they generalise.

- **Past-price features leak through tick rounding.** Including `recent_move` and
  `micro_dev` in a forward-return regression produced OOS R² of **+0.037 on synthetic data
  with no signal at all**. The integer-rounded mid creates spurious mean reversion. Both were
  removed from `book_alpha.py`.
- **Event detection leaks backwards.** Marking a jump where the *detection window* opens
  rather than where the price *starts moving* puts part of the move inside the "pre-jump"
  lookback. `precursor.py` now walks back to the last flat point and applies a buffer.
- **Event-based sampling oversamples bursts.** Sampling per book update rather than on a time
  grid inflated one dataset ~9×, since updates cluster during activity.
- **Targets need volatility normalisation.** A model fitted on a volatile match and tested on
  a calm one gets direction right and magnitude badly wrong: sign accuracy 0.615 alongside R²
  of −0.58. Predicting move-over-recent-volatility fixes the scale mismatch.

Every analysis module was validated against synthetic data with a known answer — both a
planted signal and a null — before being run on real data.

---

## Reproducing

```bash
pip install numpy pandas matplotlib
python capture/recorder.py                     # collect data
python -c "import backtest; backtest.run_backtest(...)"
```

Recorded data is not included: it is large, and redistribution sits awkwardly with the
exchange's terms. `recorder.py` collects equivalent data from a Kalshi API key.

---

## Status and limitations

- Most empirical results are measured on **one to three matches**, single contract leg. They
  are consistent with each other and with theory, but cross-match replication is outstanding.
- Analysis has focused on the **favourite's leg** — the most liquid and therefore the hardest
  to make markets in. The underdog and draw legs are untested; the Polymarket literature
  reports a longshot spread premium that would predict a friendlier structure there.
- The **cross-leg arbitrage** implied by the sum-to-100 constraint has been checked with mid
  prices only. Whether executable prices ever cross is an open question.
- The backtest has **no market impact model**; it assumes historical flow is unchanged by the
  agent's presence. This flatters any strategy quoting at size.

---

## References

- Glosten & Milgrom (1985), *Bid, Ask and Transaction Prices in a Specialist Market*
- Kyle (1985), *Continuous Auctions and Insider Trading*
- Avellaneda & Stoikov (2008), *High-frequency trading in a limit order book*
- Cartea, Jaimungal & Penalva (2015), *Algorithmic and High-Frequency Trading*
- Bouchaud, Bonart, Donier & Gould (2018), *Trades, Quotes and Prices*
