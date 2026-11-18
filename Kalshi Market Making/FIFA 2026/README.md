# Kalshi World Cup Order Books: a Microstructure Study

Independent research on tick-level Kalshi order-book data for the FIFA World Cup 2026
match markets, run as a pre-registered study: hypotheses written down before they're
tested, splits by independent tournament group, and a sealed hold-out set.

**Status, November 2026.**
- **Stages 0–1 done.** The data layer is rebuilt, repaired and externally verified,
  and the splits are drawn.
- **Stage 2 under way:** microstructure description on event-driven bars.
- **Scope:** a microstructure study, due for completion in January 2027. A full
  trading strategy is out of scope (see *Roadmap*).

---

## Contents

- [The data](#the-data)
- [The finding that shaped everything: a defect in the book](#the-finding-that-shaped-everything-a-defect-in-the-book)
- [Results so far](#results-so-far)
- [How the study is run](#how-the-study-is-run)
- [Repository layout](#repository-layout)
- [Reproducing](#reproducing)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [References](#references)

---

## The data

A recorder (`capture/recorder.py`) logged Kalshi's raw WebSocket feed (order-book
snapshots, deltas, trades and the exchange `ticker` channel) from **June 1 to June
28, 2026**. Each line is the message exactly as received, plus a local receive
timestamp.

- **72** group-stage fixtures exist; **47** were recorded with usable coverage; **40**
  passed the original quality gate.
- **Recording gap:** June 16 23:08 UTC → June 22. Matches played inside it are missing.
- Each match has three mutually exclusive contracts (home win, away win, tie), whose
  prices sum to about 100¢.
- **Prices are whole cents.** All 3.17M in-play trades in the research pool are at
  1–99¢, with no sub-cent prints. (The tournament-winner series does trade in tenths
  of a cent.) **Sizes are fractional:** about 80% of trades are non-integer, straight
  from the feed's fixed-point fields.

Raw data isn't included: it's about 14 GB, and redistribution sits awkwardly with the
exchange's terms. The recorder collects equivalent data with a Kalshi API key.

---

## The finding that shaped everything: a defect in the book

The first build reconstructed each order book by applying deltas to an **empty**
starting book. Any size resting before the recorder's first message was invisible.

**How it was found.** Per price level, the cumulative sum of deltas went **negative**:
−1,146,471 contracts at one level. That's impossible unless size existed that the
build never saw. The touch looked fine, so the usual checks all passed: settlement,
crossed books, and the three legs summing to 100. Meanwhile the deep book was wrong in
**40 of 40** matches.

**The repair.** Each book is now seeded from the last *populated* snapshot in the
tapes, where one exists without a recording gap in between.

**Verification against an independent source.** Every leg was compared, state by
state, with a third-party reconstruction (Predexon): **31 million** comparisons.

| Tier | Matches | Median ladder agreement | Supports |
|---|---|---|---|
| **depth_clean** | **27** | **99.91%**, every leg > 99.5%; deep book 100% | Everything: prices, sizes, queues, depth, flow |
| price_flow_only | 9 | 27.6% (touch 76.9%) | Prices, trades, flow; *not* depth (no clean seed behind the June 17–21 gap) |
| gaps | 4 | 20.3% | Nothing safely: dropped messages |

80 of the 81 depth_clean legs were verified externally. The remaining one had a
single third-party snapshot; it's kept, marked unverified. Per-leg results are in
`results/leg_quality.csv` and `results/state_cmp_all.csv`, and the full account is in
`docs/canonical_data_layer.md`. **All research uses the 27 depth_clean matches only.**

---

## Results so far

Full mechanisms, tests, kill criteria and results are in `hypotheses.md`.

| | Hypothesis | Status |
|---|---|---|
| **H1** | Buying all three legs is ever cheaper than 100¢ (arbitrage) | **Rejected.** On consistent states the minimum ask-sum is exactly 100.00 across 310k states; apparent arbitrage was a reconstruction artefact |
| **H8** | Liquidity at the best quotes disappears mainly through *cancellation*, not trading | **Supported, and replicated on the repaired data.** Cancelled > traded volume in **21 / 21** matches and **9 / 9** groups; median cancel share **93.1%** (IQR 3.8 pp, range 85.4–97.5%) |
| **H10** | A third party's snapshots can be differenced into usable deltas | **Void.** The test was flawed, but running it exposed the book defect above |
| **H11** | Price moves transmit between simultaneous matches in the same group | **Rejected.** A goal moved the simultaneous same-group match in only 1 of 10 pairs |
| **H12** | Matches co-move through team strength: same team +, future opponent − | **Partial.** Same team > 0 in 9/10 groups, but future opponent < 0 in only 7/10. Co-movement is group-wide, not team-specific. Estimated with Hayashi–Yoshida to handle asynchronous updates |
| H2–H7, H9 | Team similarity, spread/queue replication, thin legs, fee economics by price level, regimes, calendar effects, cancels ahead of adverse moves | Open: Stage 2 and 3 |

**Other measured facts:**

- **Kalshi's weekly maintenance is a data contaminant.** Every Thursday 07:00–09:00 UTC
  (plus a re-quote hour), orders are cancelled and re-posted across all markets at
  once. It produced the largest eigenvalue of the cross-match correlation matrix,
  11.4, which fell to 4.4 once those hours were masked. Hourly or coarser analyses
  mask Thursdays 07:00–10:00 UTC.
- **Recurring order sizes.** The same cancel sizes recur across unrelated matches: a
  typical cancel of 888 in 5 matches, 2,500 in 4 and 444 in 2. In 40 of 63 legs, the
  90th-percentile cancel is exactly 50,000. These look like participant footprints or
  venue limits; under investigation.
- **Fees.** Taker fee = 0.07 × C × P(1 − P). For World Cup match markets the maker fee
  was 0.0175 × C × P(1 − P), a quarter of the taker fee; the default for most series
  is zero. Near 50¢ a maker gives back about 0.44¢ of each 1¢ of spread; near 5¢ or
  95¢, about 0.07¢.

---

## How the study is run

The design follows the approach in *Advances in Financial Machine Learning*:

- **Pre-registration.** Each hypothesis is written in `hypotheses.md` with its
  mechanism, test and kill criteria *before* it's run. Results added afterwards are
  labelled post-hoc and can't change a verdict.
- **The unit of independence is the tournament group,** a connected component of the
  full 72-fixture team graph. Matches within a group share teams, so a per-match split
  would leak.
- **Splits (v2).** A pool of **21 matches in 9 groups** for combinatorial purged
  cross-validation, and **6 matches in 2 groups sealed**, to be used once as a final
  check. The loader refuses to read sealed matches and logs every read. See
  `docs/splits.md`.
- **Known-answer tests first.** Every estimator is checked on synthetic data with a
  planted answer before touching real data. For example, Hayashi–Yoshida recovers a
  planted ρ = +0.6 as +0.63 and is unbiased at ρ = 0, while a 1-minute grid
  correlation collapses toward 0 (the Epps effect).
- **Decisions are recorded before results.** For example, whether the one unverified
  sealed leg stays in the sealed set (it does; sealed results will be reported with
  and without it).

---

## Repository layout

```
README.md                 this file
hypotheses.md             the register: every hypothesis, test, kill criterion and result
capture/
  recorder.py             WebSocket recorder (credentials from environment variables)
pipeline/
  stage0_inventory.py     one pass over every tape: what's there
  stage0_windows.py, make_windows.py   match windows (kickoff, final whistle)
  canon.py                DualBook reconstruction core (YES/NO books)
  stage0_reconstruct.py   rebuild books per day; SEED_EXTEND seeds from snapshots
  stage0_validate.py      which matches can be trusted
  stage0_consistency.py   cross-leg consistency per state
  run_*.sh, order.txt     resumable drivers for the Stage 0 passes
  validate_sizes.py       cumulative-delta deficit per level (the defect detector)
  compare_all.py, compare_states.py   every leg vs the third-party reconstruction
  predexon_*.py, synpath_*.py         external data-source probes (H10)
  canon_load.py           the only sanctioned read path; tier warnings, sealed guard
  stage1_splits_v2.py     group-level splits (stage1_splits.py = superseded v1)
  flow_decomp.py          classify book changes as fill / cancel / add
  test_h8.py, test_h8_v2.py           H8 and its replication
  ticker_extract*.py      exchange best bid/ask panels (sealed groups dropped)
  xmatch.py               H11
  xmatrix.py              H12: Hayashi–Yoshida matrix, walk-forward, Marchenko–Pastur
  learn/hy_walk.py        readable rewrite of the walk-forward correlations
notebooks/
  walk_forward.ipynb      step-by-step walk-forward correlation matrices
docs/
  canonical_data_layer.md the data layer, tiers and repair in full
  splits.md               split design and the one rule for sealed data
  guides/                 study notes: volatility; event-driven bars
results/                  small result tables (leg quality, validation, H8, H11, H12, splits)
early_exploration/        June 2026 market-making prototypes, pre-repair (see its README)
```

---

## Reproducing

The pipeline expects a data folder laid out like this:

```
<data>/
  kalshi-2026-06-DD.jsonl[.gz]   raw tapes from capture/recorder.py
  pipeline/                      this folder
  canonical/                     built by Stage 0
  splits/                        built by Stage 1
```

```bash
pip install numpy pandas "pyarrow>=18" scipy matplotlib
bash pipeline/run_inventory.sh                        # re-run until it prints COMPLETE (time-budgeted)
bash pipeline/run_windows.sh                          # same
OUT_SUFFIX=_v2 SEED_EXTEND=1 bash pipeline/run_reconstruct.sh   # repaired build; day files in order (pipeline/order.txt)
CANON_SUFFIX=_v2 python3 pipeline/stage0_validate.py
python3 pipeline/stage1_splits_v2.py
CANON_SUFFIX=_v2 python3 pipeline/test_h8_v2.py run && CANON_SUFFIX=_v2 python3 pipeline/test_h8_v2.py summary
python3 pipeline/learn/hy_walk.py selftest
```

The `run_*.sh` scripts stop after a time budget and resume where they left off, so
run them repeatedly until they report completion.

`CANON_SUFFIX` is required: it names the tree to read (`_v2` = depth_clean), and
reads fail without it. External comparisons need `PREDEXON_API_KEY` in the
environment; it's never stored in code.

---

## Roadmap

| Phase | When | Work |
|---|---|---|
| Bars | Nov 2026 | Tick, volume and book-event bars; compare their statistics with time bars |
| Stylised facts | Nov – Dec | Spreads, depth, trade and cancel rates, price impact, by leg type and match phase (H3, H4, H6); a spread-minus-fees cost map by price level (H5) |
| Jumps and one predictive test | Dec – Jan | Jump detection and the book around goals; H9; a registered test of whether book imbalance predicts the next mid move, with grouped purged CV |
| Sealed check and write-up | Jan 2027 | One check on the sealed groups; final report |

Out of scope for this study, listed as future work: a full strategy backtest with a
fill model, meta-labelling, a volatility forecaster, and fresh-data tests on live
leagues.

---

## Limitations

- **Few independent events.** 27 clean matches in 11 groups. Rows are plentiful;
  independent observations are not.
- **No knockout rounds**, and nothing from June 17–21.
- **L2 data only.** Kalshi publishes aggregate size per price level, not individual
  orders, so queue position can't be observed directly.
- **The markets have settled.** Nothing here can be traded live; any edge would have
  to carry over to other Kalshi sports markets.
- The **maker fee schedule** on file took effect July 7, after the group stage; the
  June schedule is unconfirmed, so the 0.0175 maker rate is treated as the
  conservative case.

---

## References

- López de Prado (2018), *Advances in Financial Machine Learning*
- Hayashi & Yoshida (2005), *On covariance estimation of non-synchronously observed diffusion processes*
- Epps (1979), *Comovements in stock prices in the very short run*
- Laloux, Cizeau, Bouchaud & Potters (1999), *Noise dressing of financial correlation matrices*
- Clark (1973), *A subordinated stochastic process model with finite variance for speculative prices*
- Glosten & Milgrom (1985); Kyle (1985); Avellaneda & Stoikov (2008)
- Bouchaud, Bonart, Donier & Gould (2018), *Trades, Quotes and Prices*
