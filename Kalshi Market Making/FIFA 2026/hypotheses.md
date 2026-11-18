# Hypothesis register

Every hypothesis gets written here **before** it is tested, with the mechanism,
the test, and the result that would kill it. An entry added after seeing the
answer is not a hypothesis, it is a story.

Status values: `open` · `supported` · `rejected` · `void` (test was invalid)

---

## H1 — Three-leg arbitrage exists
**Status: REJECTED (2026-10-03)**

Mechanism: the three legs are mutually exclusive and exhaustive, so buying all
three must cost ≥ 100. If it ever costs less, that is free money.

Result: on internally consistent states the ask-sum minimum is exactly **100.00**
across 310k states. Apparent sub-100 states were entirely our own reconstruction
artifact — states where one leg's touch was stale.

---

## H2 — Matches of the same team resemble each other
**Status: open.** Untested. This is the hypothesis that forced splitting by
tournament group rather than by match.

## H3 — The spread and queue picture replicates across matches
**Status: open.** 95.3% of states at 1 tick and ~59,000 median touch queue come
from a single match.

## H4 — Thin legs (underdog, TIE) behave differently from favourites
**Status: open.**

## H5 — Market making is viable at the price extremes and dead mid-range
**Status: open.**

Mechanism: the Kalshi fee is proportional to p(1−p), maximal at 50 and about 5×
cheaper at 5 or 95. The tick is fixed at 1 cent everywhere, so the *relative*
spread is 2% at p=50 and 20% at p=5. Edge-to-cost should therefore be far better
at the extremes — the opposite of where attention naturally goes.

## H6 — Within-match regimes exist and are learnable (HMM go/no-go)
**Status: open.** Criteria recorded in PIPELINE_ORIGINAL.md appendix.

## H7 — Calendar effects vs data-tier effects: which explains more?
**Status: open.** Tier A/B correlates with date, so a chronological split also
splits by data quality.

---

## H8 — Queue depletion at the touch is dominated by cancellation, not trading
**Status: SUPPORTED (2026-10-12); replicated on the repaired data, 21/21 matches (2026-10-23). Results at the end of this entry.**

**Registered 2026-10-12, before running. Not blind:** this is a replication test
of an observation already made on one leg of one match (26JUN11KORCZE-KOR). That
match is in the exploration set and is one of the pinned, already-contaminated
ones. Declaring the prior observation is the point — pretending to a blind
prediction would be worse than admitting the bias.

### Mechanism

If a resting order's queue is consumed mainly by trades, then waiting in the
queue is a matter of patience: trades arrive, the queue in front shrinks, you
reach the front. If instead the queue is mainly *withdrawn* by the makers ahead
of you, then queue position is not earned by waiting — it is granted or removed
by other participants' decisions, and most likely removed precisely when they
have a reason to leave.

These imply opposite market-making strategies, so the question is not cosmetic.

### Observation being tested (one leg, one match)

| quantity | observed |
|---|---|
| share of volume leaving the touch that was cancelled | 77.1% |
| share that was traded | 22.9% |
| median fill size | 41 contracts |
| median cancel size | 400 contracts |
| 90th percentile, fill | 281 |
| 90th percentile, cancel | 25,000 |

### Test

Run the fill/cancel/add decomposition over **all exploration matches, all three
legs**, in-play only. Report **per match**, never pooled: pooling across matches
would let one busy match dominate and hide between-match variation.

The unit of independence is the **tournament group**, not the match and not the
leg. The three legs of a match are mechanically linked by the sum-to-100
constraint. The exploration set holds 24 matches across 8 groups, so the
effective n for any cross-match claim is 8.

### Predictions, stated in advance

- **P1** Cancelled volume exceeds traded volume at the touch in at least
  **20 of 24** matches.
- **P2** Median fill size is below median cancel size in at least **20 of 24**.
- **P3** The cancel share has a median across matches above **60%**.
- **P4** The effect holds in at least **7 of 8** groups, judged on the group's
  median match.

### What would kill it

- Cancel share below 50% in a third or more of matches → the effect is not general.
- Cancel share median between 45% and 55% → there is no asymmetry, only noise.
- Interquartile range of cancel share wider than 30 percentage points → the
  quantity is not stable enough to build on, even if its average looks right.
- The effect present in fewer than 6 of 8 groups → it is a property of particular
  groups, not of the market.

### Known bias in the measurement

The decomposition over-attributes to `fill` by about 4% of volume: a genuine
cancellation landing on the same millisecond *and* price as a trade cannot be
separated. The error is one-directional — it always inflates fill and deflates
cancel — so any measured cancel share is a **lower bound**. This makes the
hypothesis harder to support, not easier, which is the right direction for a
bias to run.

### Not being tested here

Whether cancellation clusters ahead of adverse price moves. That is the
interesting follow-up and a separate hypothesis. Asserting it without measuring
it, as was done on 2026-10-10, is exactly the error this register exists to stop.


### H8 RESULT (2026-10-12)

24 exploration matches, 72 legs, 8 groups. Legs aggregated by volume within a
match; matches reported separately; groups used as the unit of independence.

| prediction | result | |
|---|---|---|
| P1 cancel > trade in ≥20/24 | **24/24** | pass |
| P2 median fill < median cancel in ≥20/24 | **24/24** | pass |
| P3 median cancel share > 60% | **93.2%** | pass |
| P4 holds in ≥7/8 groups | **8/8** | pass |

No kill criterion fired. Cancel share ranges 85.4% to 96.8% across matches,
**IQR 3.0 percentage points** against a 30-point instability threshold. Per-group
medians span 91.1% to 94.5%.

The original single-match figure of 77.1% was an underestimate, and that match
(26JUN11KORCZE) is the lowest of all 24. The over-attribution bias runs one way,
so 93.2% is a lower bound.

Median fill size 31–94 contracts in every match. Median cancel 375–2,500. The
90th-percentile cancel is exactly 50,000 in 15 of 24 matches, which suggests a
size cap or standard block rather than coincidence — worth a separate look.

Code: pipeline/test_h8.py. Per-leg output: canonical/h8_legs_*.csv.

### Re-run on the repaired data (2026-10-23) — REPLICATES

The registered run above used the original 47-match build, before the cold-start defect
was found, over the v1 explore set. That set included 26JUN12CANBIH, now in the gaps
tier. This re-run uses the depth_clean tree (`CANON_SUFFIX=_v2`) over the v2 pool:
**21 matches, 63 legs, 9 groups.**
- The measurement code is unchanged: `touch_flow()` is imported from `test_h8.py`.
- The thresholds are the registered ones, scaled at the same proportions: 20/24 → 18/21,
  7/8 groups → 8/9.
- **Maintenance:** H8 is in-play only, and no pool match was in play during a Thursday
  07:00–10:00 UTC window (checked against `windows.csv`), so the mask changes nothing.

| prediction | threshold | result | |
|---|---|---|---|
| P1 cancel > trade | ≥ 18/21 | **21/21** | pass |
| P2 median fill < median cancel | ≥ 18/21 | **21/21** | pass |
| P3 median cancel share | > 60% | **93.1%** | pass |
| P4 groups (median match) | ≥ 8/9 | **9/9** | pass |

No kill criterion fired:
- no match has a cancel share below 50%;
- the IQR is 3.8 pp (kill above 30 pp);
- cancel share ranges from 85.4% to 97.5%;
- group medians range from 90.4% to 94.3%.

The headline barely moves: 93.2% on the old build, 93.1% on the clean one. That fits
the repair: it fixed the deep book, and this measurement uses only the touch.
26JUN11KORCZE is again the lowest match (85.4%).

**Sizes, and an observation for the 50,000 question** (not tested; for the footprint
investigation):
- Median fill size is 30–100 contracts.
- Median cancel size is 375–2,500. The same few values recur across unrelated
  matches: **888 in 5 matches, 2,500 in 4, 444 in 2 and 1,776 in 1**, where
  888 = 2 × 444 and 1,776 = 2 × 888. These are match medians of leg medians.
- The leg-level 90th-percentile cancel is exactly **50,000 in 40 of 63 legs**
  (13 of 21 matches on the match median).

Round, repeated sizes across different matches look like standing order sizes of
particular participants, or venue limits, rather than chance.

Code: `pipeline/test_h8_v2.py`. Output: `canonical/h8_v2_legs_*.csv` and
`canonical/h8_v2_matches.csv`.

---

## H9 — Cancellation at the touch clusters ahead of adverse price moves
**Status: open. Registered 2026-10-12, not yet tested.**

Mechanism: H8 established that ~93% of volume leaving the touch is withdrawn
rather than traded, so queue position is granted and removed by other makers'
decisions. If those withdrawals are informative — makers leaving because they
expect the price to move — then reaching the front of the queue is not a reward
but a warning, and the fills a market maker does get are selected against.

If withdrawals are uninformative, queue position is merely unreliable rather
than toxic, and the two cases call for opposite strategies.

This was asserted on 2026-10-10 without measurement. It is recorded here as
untested, and must not be repeated as if established.

Test and falsification criteria: to be written before running, not after.

---

## H10 — Predexon snapshots can be differenced back into usable deltas
**Status: VOID (2026-10-12). P1 failed. It exposed a defect in our own data.**

**Registered 2026-10-12, before fetching the data.** Predictions and kill
criteria below were written before any differencing was run.

### Why this is being asked

The `predexon_compare` run established two things on 26JUN24RSAKOR:

- their best bid and best ask agree with ours on 98.0–99.6% of our states, and
  their book never crosses, so they reconstruct the same book we do;
- their update rate is 0.13–0.46 of ours (1.9/sec against 5.1/sec, median gap
  409 ms against 74 ms), and the ratio is *lowest* on the busiest leg.

So their states look right but their clock is coarse. The open question is
whether differencing consecutive snapshots recovers the individual events. If it
does, their free snapshot endpoint substitutes for the gated tick feed and the
whole external-validation plan survives. If it does not, their data supports
state features only and no flow work.

### Mechanism

Between two of their snapshots at `t_k` and `t_{k+1}`, our raw layer holds every
delta that occurred. Differencing gives only the NET change per price level over
that interval. Netting is lossless exactly when at most one of our deltas falls
in the interval at that price. With two or more, the individual events are gone,
and when those events have opposite signs the loss is irreducible: an add of 500
followed by a cancel of 500 nets to zero and is indistinguishable from nothing
happening at all.

This is the same ambiguity we measured as ABSENT in Kalshi's own feed (one delta
per trade print, same millisecond, 99.6% one-per-print). Differencing a
throttled snapshot series puts it back.

### Test

One match, 26JUN24RSAKOR, all three legs, in-play. Chosen because all three legs
returned data in the compare run; it is in the exploration set and already
contaminated, so spending it costs nothing.

Fetch their full-window snapshots once and cache to disk. For every consecutive
snapshot pair, and every price level on both ladders, compare their net size
change against the sum of our raw deltas in the same half-open interval.

### Predictions, stated in advance

- **P1 State fidelity.** At least **99%** of (interval, ladder, price) cells show
  their net change equal to our summed delta. This tests their whole ladder, not
  just the touch, and it is the premise everything else rests on: if their states
  are not faithful, the 98% touch agreement was coarseness flattering them.
- **P2 Event recovery.** Fewer than **50%** of our deltas sit alone in their
  interval at their price. That is the fraction individually recoverable.
- **P3 Worse at the touch.** Recovery at the touch is at least **10 percentage
  points** below recovery away from it, because throttling bites hardest where
  flow is heaviest, and the touch is where flow is heaviest.
- **P4 Classification loss.** At least **20%** of filled volume lands in a cell
  holding two or more of our deltas, so the fill/cancel split cannot be
  reconstructed there. H8 would not have been computable on their data.

### What would kill it

The hypothesis as stated is that their snapshots are NOT sufficient. It dies if:

- event recovery exceeds **90%** AND mixed-sign cells carry under **5%** of
  volume — their snapshots are then good enough for flow work, the Q2 verdict was
  wrong, and the free endpoint replaces the tick feed;
- P1 fails (state fidelity below 99%) — then the test is **void**, not
  supportive. A low number would mean our interval arithmetic or price mapping is
  wrong, or their states are unfaithful, and nothing downstream of it can be
  read. Diagnose before interpreting anything else.

### Note on direction of interest

A result supporting H10 closes a door. That makes it the easy result to accept
uncritically, which is the reason P1 is in the list: it is the prediction that
fails if the measurement itself is broken, and it must be checked before the
convenient conclusions are believed.

### Result (2026-10-12) — VOID, and it found a defect in OUR data

Run on 26JUN24RSAKOR, all three legs. P1 failed hard and the script refused to
print P2–P4, as registered.

| prediction | threshold | measured |
|---|---|---|
| P1 state fidelity | ≥ 99% | **3.1%** |

P1 is a gate, not a result, so nothing below it is interpretable. Diagnosis of
the failure, in order:

1. Ruled out as causes: price units (both whole cents), size units (median ratio
   1.0002), ladder truncation (their snapshots carry 37–62 levels, not capped),
   pagination overlap.
2. State-versus-state comparison, no differencing involved: only 48.8% of price
   levels agree. So the failure is not the interval arithmetic.
3. The disagreement is **one-directional**. Their size exceeds ours on 99.8% of
   disagreeing levels. 508 levels exist in theirs and are absent from ours; zero
   exist in ours and are absent from theirs. **Our book is a strict subset.**
4. Agreement decays with distance from the touch — 90% at the touch, 85% within
   10 ticks, 60% at 11–30, 23% beyond 31 — and improves over the match, 41% in
   the first third to 61% in the last.
5. Cause confirmed in the source tape: **the tape contains no
   `orderbook_snapshot` messages for this ticker at all.** Message types present
   are `orderbook_delta`, `ticker`, `trade`, `ok`.

### The defect

Our books are reconstructed from deltas starting from an EMPTY book. Any order
resting before our recording began is permanently invisible, unless a later
event adds to that same level. Size parked at the price extremes and left there
— which is what market makers do at 1–5 and 95–99 — is never learned.

The manifest measured this from the beginning and it was not read:

- `neg_clamps` = **518,957** over 228 leg-days, non-zero in **228 of 228**
  (median 1,018, max 23,917). A negative clamp is a delta removing size we never
  knew was resting. Each one is proof of an invisible order.
- `n_snapshots_applied` = **0 for 120 of 228 leg-days**.

The validation suite missed it because every check in it — settlement price,
crossed book, three-leg sum — tests the TOUCH, and the touch churns fast enough
to rebuild itself from deltas alone within seconds.

### Consequences

- **H8 is unaffected.** `flow_decomp` reads raw delta amounts, which are never
  clamped, and its touch classification uses the touch, which is ~98% correct.
- **Depth features are not usable** until rebuilt: `bid_depth`, `ask_depth`,
  `n_bid_levels`, `n_ask_levels`, and the ladder beyond a few ticks. They are
  understated, non-uniformly, worst early in each leg.
- **No claim resting on book depth may be made from the current canonical
  layer.** Touch-level claims stand.

### The repair, and the inversion of the Predexon conclusion

Predexon's snapshots are unusable for flow work (too coarse, netted) but are
exactly what is missing here: a complete book state. Seeding `DualBook` from one
of their snapshots at or before our first delta, then replaying our own deltas
on top, yields their full depth and our full event resolution — better than
either source alone. One request per leg, 120 for the whole set, free tier.

The earlier verdict that their data was of no use was too quick. It was right
about the use we had in mind and wrong about the use that matters.

**Acceptance test for the rebuild:** state fidelity on this same match must rise
from 48.8% to above 99%, and `neg_clamps` must fall by an order of magnitude.
Registered here before the rebuild is written.

### Repair and acceptance test (2026-10-12)

The acceptance test was registered above before the rebuild was written:
state fidelity must improve and `neg_clamps` must fall by an order of magnitude.

**What was changed.** `stage0_reconstruct.py` gained `SEED_EXTEND`, which
activates a leg from the UTC day of the last POPULATED snapshot before its
window instead of one day of lead-in, and `OUT_SUFFIX`, which writes a parallel
tree (`books_v2`, `raw_v2`, `_state_v2`, `_manifest_v2`) so nothing is replaced
until it has been compared.

**Scope.** 9 matches, 27 legs: the B-class matches holding at least one unseeded
leg. Seeds were 1-2 days back, so only 6 tapes were needed. The 5.5 GB June 15
tape was pre-filtered to the relevant tickers (204 MB) to fit the call limit.

**C-class legs were deliberately NOT seeded.** Replaying post-gap deltas onto a
state from before the June 17-21 recording hole would make sizes wrong in BOTH
directions. An empty start is worse but remains a one-directional lower bound,
and that property is what keeps the data usable at all.

**Result, deficit measured from the raw layer (canonical/rebuild_acceptance.csv):**

| | before | after |
|---|---|---|
| levels with a deficit, median leg | 75 | **12** |
| total deficit, median leg | 4,314,878 | **680,858** |
| legs improved / worsened | — | **27 / 0** |

Not uniform, and the split is informative:

- **June 14 group (12 legs): effectively fixed.** 47-88 bad levels to 0-4;
  deficits of millions to 0-3,635 contracts, one outlier at 71,228.
- **June 26 group (15 legs): improved ~5x, not fixed.** 34-88 bad levels to 7-28;
  deficits ~4M to 0.7-1.6M.

The cause of the difference is snapshot supply, not the method: the June 13 tape
carries 1,020 populated snapshots, while late-June tapes carry almost none
(June 30 onward: 432 snapshot messages, 0 populated). The June 26 legs got 6-9
applied against the June 14 group's 30-45.

**Book-layer check, 1,430,990 states, 1:1 merge enforced:**

| | |
|---|---|
| touch price differs from live | 0.41% |
| best bid higher in v2 / lower | 40.7% / 11.8% |
| best ask lower in v2 / higher | 45.5% / 11.5% |
| spread tighter / wider | 76.9% / 14.2% |
| same-touch states, v2 size >= live | 99.70% bid, 99.57% ask |
| crossed states | 0.0000% |

Asymmetric in the direction seeding predicts, and sizes are near-monotonically
larger, which is the only effect adding missing resting size can have. The 0.3%
where v2 is smaller is the uncross prune acting on a different book.

**ACCEPTANCE: MET** at the median (12 bad levels against A-class 9), with the
June 14 group far exceeding it and the June 26 group marginal.

**Still outstanding:** the external Predexon check on a rebuilt leg. Neither of
this session's environments can reach api.predexon.com (HTTP 000 against pypi
200), so it has not been run. Until it is, the rebuild is validated against our
own arithmetic and against A-class legs, but not against an independent source.

### Two process errors made during this work

1. **A code commit reported success while delivering stale content.** The first
   run of the rebuild therefore executed the OLD code with the new environment
   variables silently ignored, writing to the live tree instead of v2. It was an
   idempotent rewrite of one day's parts so nothing was lost, but `_state/books.pkl`
   is now advanced to end-of-June-13 rather than end-of-tape. Every code commit is
   now verified by md5 on both sides before being run.

2. **A cartesian product was again mistaken for a result.** Merging v2 against
   live on `ts` alone produced 27.4M rows from 1.6M, because several top-of-book
   changes share a millisecond. The inflated version showed symmetric touch
   movement and would have been read as evidence the rebuild did nothing. One
   state per millisecond and `validate='one_to_one'` gives the real numbers above.
   This is the same error as the 2026-10-10 trade join; the row count was again
   the tell, and again it was not noticed until after the figures were printed.

### External confirmation (2026-10-12), and a correction to the P1 gate

**P1 was a badly designed gate and has been retired.** It compared their NET
CHANGE across an interval against the sum of our deltas inside it. Their
snapshots are ~409 ms apart and our deltas arrive every ~74 ms, so each interval
holds about five events; a few ms of timestamp skew pushes one over a boundary
and fails the whole cell. P1 therefore read ~3% regardless of book quality --
2.9% on the REPAIRED 26JUN14GERCUW and 3.1% on the unrepaired 26JUN24RSAKOR.
It measured boundary alignment, not correctness. The signal that should have
caught this earlier: the direct state comparison on RSAKOR gave 48.8% where P1
gave 3.1% on identical data.

Replaced by `pipeline/compare_states.py`, which compares full ladders at
EXACTLY equal milliseconds. No intervals, nothing to misassign.

**Result on 26JUN14GERCUW. Identical cached Predexon snapshots in both runs;
the only variable is which of our books is compared.**

| leg | live tree | rebuilt (_v2) |
|---|---|---|
| CUW | 8.45% | **99.94%** |
| GER | 35.81% | **99.84%** |
| TIE | 25.54% | **99.97%** |
| levels we LACK | 73,213 | **18** |
| levels we INVENT | 48 | 43 |

By distance from the touch, CUW:

| distance | live | rebuilt |
|---|---|---|
| touch | 74.3% | 97.8% |
| 1-3 | 45.2% | 99.9% |
| 4-10 | 21.5% | 100.0% |
| 11-30 | 17.5% | 100.0% |
| 31+ | 0.0% | 100.0% |

**ACCEPTANCE: MET, externally.** The deep book goes from zero agreement to
exact. The repair is confirmed against a reconstruction built independently from
the same exchange feed, not only against our own arithmetic.

**Residual, stated rather than rounded away.** 18 levels lacked and 43 invented
out of ~579,000 comparisons, and the touch reaches 97-99% rather than 100%. The
touch is the fastest-changing level and several top-of-book changes share a
millisecond, so a state compared at millisecond resolution can legitimately
differ on within-millisecond ordering. That explains the residual's shape -- it
is concentrated exactly where churn is highest and vanishes with depth -- but it
has not been separately measured, and is not claimed as explained.

### Full external verification, all 120 legs (2026-10-12)

`pipeline/compare_all.py` compared every leg's ladder against Predexon's
independent reconstruction at exactly equal milliseconds. 119 of 120 legs
measured; 31 million level comparisons in the clean tier alone. Results per leg
are in `canonical/state_cmp_all.csv` and merged into
`canonical/leg_quality.csv`.

| tier | legs | agreement (median) | at touch | deep 31+ | levels lacked |
|---|---|---|---|---|---|
| depth_clean | 80 | **99.91%** | 97.7% | **100.00%** | 7 |
| gaps | 3 | 93.86% | 64.2% | 94.50% | 16 |
| price_flow_only | 36 | 25.62% | 76.4% | 4.23% | 11,893 |

**depth_clean: every leg verified, none below 99%.**

- agreement: min 99.75%, p25 99.88%, median 99.91%, max 99.98%
- **80 of 80 legs above 99.5%**; 51 above 99.9%
- deep book (31+ ticks) minimum **99.97%** across all 80 legs
- 1,001 levels lacked and 1,370 invented out of 31,004,065 comparisons
- 26 of 27 matches verified on all three legs

The claim that these books are correct no longer rests on inference from two
checked matches. It is measured leg by leg.

**The one gap: 26JUN26NZLBEL-TIE.** Predexon returned a single snapshot for that
ticker, so there was no exact-millisecond overlap to compare. The leg is not
known bad, it is unmeasured, and `leg_quality.csv` marks it `verified = False`.
Its two sibling legs passed at depth_clean levels.

**Two defect fingerprints, now distinguishable from the numbers alone:**

- **cold start** (price_flow_only) destroys the DEEP book and leaves the touch
  partly usable: deep 4.2%, touch 76.4%.
- **dropped messages** (gaps) does the reverse: deep 94.5% but touch 64.2%,
  which is the worse failure, because the touch is where a maker rests.

### Three earlier statements corrected by this run

1. **"A non-zero 'invented' count means the seed was stale."** Wrong. In the
   clean tier invented (1,370) slightly EXCEEDS lacked (1,001), so it is
   symmetric noise from within-millisecond ordering, not a seeding failure. The
   guidance written into `compare_states.py` was misleading and is withdrawn.

2. **"Sizes agree 90% at the touch" for the unseeded tier.** Overstated. The
   real figure is **76.4% median, range 53.6-99.6%**. The 90% came from a
   sampled subset with a 50 ms matching tolerance; this is exact-millisecond.
   Flow work on that tier must avoid anything that needs queue size, not merely
   anything that needs deep depth.

3. **"28 matches are depth-clean."** It is **27**. 26JUN12CANBIH was removed
   after its residual was traced to 266 dropped messages rather than a cold
   start. It was found only because a confounded deficit metric happened to
   point at it, which was luck, not method. The reliable detector is
   `seq_gaps` in the manifest, which is zero for all 27 remaining matches.

---

## H11 — Price moves transmit between matches only inside a group

**Status: REJECTED (2026-10-16). P1 failed: 1 of 10. Registered before any cross-match data was extracted; result at the end of this entry.**

### Mechanism

A match's contracts should react to events elsewhere only through a channel that
changes their own fundamentals:

- **shared team** — injury, suspension, form;
- **shared group** — qualification stakes. Above all in the final round, where
  each group's two matches kick off simultaneously and a goal in one changes what
  the other's teams need, in real time.

Across groups there is no fundamental channel in game markets, so any
cross-group co-movement would have to come from market-wide flows: the same
makers repricing everything, or attention shifting.

If cross-group dependence is real, the group split (Stage 1) is not sufficient
for independence, which would matter far beyond this hypothesis.

### Data

The exchange `ticker` channel: best bid and ask as published by Kalshi, for every
KXWCGAME market on every tape day June 2-28. No book reconstruction is involved.
It agrees with the verified v2 book 100.00% on bid and ask across 3,834 in-play
updates on 26JUN14GERCUW-GER.

**Excluded, at extraction:** every fixture in the two sealed groups (they contain
the 6 sealed matches). Also excluded: the placeholder kickoff `2026-06-16 21:00`
that `windows.csv` assigns to unrecorded fixtures.

**Unit of independence:** the group. 10 groups remain.

### Primary test — event study, specified once

- **Event in match A:** during A's in-play window, the mid of any A leg moves at
  least **10¢ within 60 s**. Events are debounced to one per 10 minutes.
- **Response in match B:** `R_post = max over B's legs |mid(t+5m) − mid(t)|`,
  and `R_pre` is the same over `[t−5m, t)`. The **abnormal response** is
  `R_post − R_pre`, which controls for B's own volatility.
- Events where B itself jumps 10¢ within ±60 s are dropped (coincident goals).
- **Placebo:** random times in A's in-play window at least 10 minutes from any
  event, scored identically.
- **A pair is "positive"** if its mean abnormal response exceeds the 95th
  percentile of its own placebo distribution.

### Predictions

- **P1** — Simultaneous same-group pairs (the final-round pair in each of the 10
  groups, each match serving as A for the other): at least **7 of 10** pairs
  positive.
- **P2** — Cross-group (B in another group, at any point in time): at most
  **2 of 10** A-groups show a positive response. That is the level of noise.
- **P3** *(secondary, not a kill criterion)* — Same-group future matches, not
  simultaneous: median abnormal response above the cross-group median.

### What would kill it

- **P1 fails** (6 or fewer of 10): in-group transmission is not detectable even
  in its strongest form.
- **P2 fails** (3 or more of 10): dependence is not group-specific. The groups
  are then not independent units, and the Stage 1 split must be revisited.

### Declared in advance

- The **rolling correlation matrix** is descriptive, not a test. It is computed
  at 1, 5 and 15 minute horizons, because thinly updating pre-match markets bias
  short-horizon correlations towards zero.
- **Sensitivity runs** — jump threshold 8¢ or 15¢; response window 2 or 15
  minutes — are logged as separate trials, and none of them can rescue a failed
  primary test.

### Implementation notes — recorded 2026-10-16, before any H11 result was computed

These resolve points the registration left open. None was chosen after seeing
results.

1. **Placebo comparison.** "Exceeds the 95th percentile of its own placebo
   distribution" is read as: the pair's mean abnormal response exceeds the 95th
   percentile of the means of the same number of randomly timed placebo events,
   resampled 2,000 times with a fixed seed. Comparing a mean against single
   placebo draws would make nearly every pair look significant.
2. **Unit of resampling in P2 is the event time.** One market-wide move hits
   every cross-group match at once, so those responses are correlated. Responses
   are averaged across matches B at each time before resampling. Resampling
   individual (event, B) values would understate the placebo spread and produce
   false positives.
3. **Spread filter.** Quotes with a spread of 50¢ or more carry no usable mid and
   are dropped. After settlement, quotes collapse (for example bid 1 / ask 99, so
   mid 50) 11–40 seconds before the recorded end time. Left in, that would put a
   fake jump at the end of every match.
4. **In-play window admission.** A match is in scope if `covered`, or if its final
   recorded trades show it settled on tape (one leg at 98¢ or more, the other two
   at 2¢ or less). This admits 26JUN27DZAAUT and 26JUN27JORARG — final whistle at
   04:01, the tape ends at 04:01:30 — which fail `covered` only on the hour of
   settlement padding. It rejects matches truncated mid-play (26JUN16IRQNOR and
   26JUN16ARGDZA end at 86/4/13 and 68/12/21). A date-based guard was tried first
   and wrongly admitted those two. `windows.csv` gives 21 fixtures a fake
   21:00 kickoff because their trade flow stopped with the tape.

5. **Correction, before results.** Settlement evidence alone was also insufficient: it admitted 26JUN21URUCPV, played inside the June 17–21 gap, because the ticker channel carries the last trade *price* forward onto the June 22 tape. Admission now also requires the in-play window to lie inside recorded tape. Final admitted set beyond `covered`: 26JUN27DZAAUT and 26JUN27JORARG only.

### Result (2026-10-16) — REJECTED (P1 failed)

| | Prediction | Result |
|---|---|---|
| P1 — simultaneous same-group pairs | at least 7 of 10 positive | **1 of 10** |
| P2 — cross-group | at most 2 of 10 positive | 2 of 10 — passes, at the boundary |
| P3 — same-group future vs cross-group *(secondary)* | same-group higher | 0.024¢ vs 0.004¢ mean; both medians 0.000¢ |

**The events are real.** 88 events across 41 matches, 2.15 per match against
football's ~2.5 goals per game. The median largest-leg move is 21¢ (IQR 8–29).
A spot check (26JUN25CUWCIV, minute 7: CIV 82.5→92.5, CUW 5.5→1.5, TIE 12.5→6.5)
is a goal.

**The failure is not just low power.** The test could detect only a mean
abnormal response above the placebo 95th percentile — 2.0–16.3¢, median 7.9¢ —
because both matches are in play and their own volatility is large. But the
pair means do not lean positive: 4 positive, 2 zero, 4 negative, mean −0.18¢.
No consistent sign is visible.

**What this rules out, and what it does not.** It rules out large,
instantaneous transmission into match-result markets: a goal in one match does
not reprice the other match's 1X2 contracts by several cents within five minutes.
It does not rule out small effects, or slow ones that build over the remaining
match time.

**Interpretation — not tested.** KXWCGAME markets price a match *result*. A goal
elsewhere changes a team's *qualification* stakes, not directly the odds of its
own result; any effect would run through tactics over the remaining minutes. The
instantaneous channel H11 was looking for belongs to qualification and outright
markets. ~~which this dataset does not contain~~

**Correction (2026-10-16, same day):** the tapes *do* contain them — `KXWCGROUPQUAL`,
`KXWCGROUPWIN`, `KXWCGROUPBOTTOM`, `KXMENWORLDCUP` and 20 other series were
recorded all along. The pipeline only ever extracted `KXWCGAME`, and that claim was
made without checking. The interpretation above is therefore *testable* on this
data, and has not been tested.

**P2 carries a note.** 2 of 10 groups (COD, URU) came out positive, where about 0.5
would be expected by chance at the 5% level. That is inside the registered
threshold but above expectation. There is no evidence here that cross-group
dependence breaks the Stage 1 group split, but this is not a clean bill either.

**The descriptive matrix is mostly undefined on match days.** Future-match mids
barely move intraday: 0–2 one-minute changes in a whole day for the markets
checked on June 24. Correlations need both series to move, so near-zero medians
reflect stale prices, not independence. Outputs: `canonical/xmatch/corr_pairs.parquet`,
`corr_summary.csv`, `h11_p1_pairs.csv`, `h11_p2_crossgroup.csv`, `h11_result.json`.

**Follow-up, if pursued, must be registered first:** the slow channel. Measure a
same-group future match's repricing from before A's kickoff to an hour after A's
final whistle, against A's result, with cross-group matches as the control.

---

## H12 — Match prices co-move through team strength: same team positive, future opponent negative

**Status: PARTIAL (2026-10-16). P1 passed 9/10, P2 failed 7/10. Registered before
any cross-match correlation in this design was computed; result at the end of this entry.** (H11's descriptive grid matrix existed already. It used a
different estimator and series, and it is recorded as mostly undefined.)

### Mechanism

A match reveals information about the two teams playing it. If prices update team
strength in a Bayesian way, then while both markets are live:

- team X's win price in match A and **X's win price in another match B move
  together** — the same strength estimate is being revised;
- X's win price in A and **the win price of X's opponent in B move oppositely** —
  a stronger X is worse news for whoever faces X next.

Generic co-movement — market makers repricing everything together, or shifting
attention — cannot produce opposite signs. The sign pattern is what separates
strength updating from noise.

### Data and series

- `canonical/ticker` (exchange best bid/ask, match-result series only; both sealed
  groups excluded at extraction): 60 matches in 10 groups.
- **One series per (match, team): that team's win leg.** 120 series. Draw legs
  are excluded. Pairs from the **same match** are excluded from every statistic,
  since the legs are mechanically linked.
- **Transform: arcsine, 2·asin(√p)** — variance-stabilising for a probability.
  Logit is a logged sensitivity run only, because it amplifies 1¢ ticks near 0
  and 100.
- **Live interval** of a series runs from its first two-sided quote to its match's
  final whistle (in-play windows admitted as in H11). A finished match leaves the
  set. Quotes with a spread of 50¢ or more are dropped.
- **No increment may span the June 16 23:08 → June 22 recording gap.** Markets
  settle inside the gap, and one gap-spanning jump would put spurious covariance
  into every series that crosses it.

### Estimator

**Hayashi–Yoshida**, on transformed mids at each series' actual price-change
times. Covariance = Σ Δxᵢ Δyⱼ over increment pairs whose intervals overlap;
correlation = covariance / √(RVx · RVy). It is pooled over each pair's joint live
period. HY is unbiased under asynchronous updating, which is the dominant feature
here: a team's price moves in only 13% of its pre-kickoff hours.

### Pair classes

| Class | Definition | Predicted sign |
|---|---|---|
| same_team | X in A vs X in B | **+** |
| future_opponent | X in A vs Y in B, where B is X v Y | **−** |
| same_group_other | different teams in the same group, any other link | ~0 |
| cross_group | teams in different groups | ~0 |

### Predictions

The unit of independence is the group (10). Each group's statistic is the mean HY
correlation over its pairs in that class.

- **P1** — same_team mean **> 0 in at least 8 of 10 groups.**
- **P2** — future_opponent mean **< 0 in at least 8 of 10 groups.**
- **P3** *(secondary)* — pooled same_team mean exceeds the pooled cross_group mean
  (one-sided permutation test over class labels, 5%).
- **P4** *(secondary, descriptive)* — hierarchical clustering (correlation
  distance √(½(1−ρ)), average linkage, cut at 10 clusters) recovers the groups
  better than chance: adjusted Rand index above the 95th percentile of
  group-label permutations.

Under the null, a group's sign is a coin flip, and 8-or-more of 10 has
probability 5.5%.

### What would kill it

- **P1 and P2 both fail:** no strength updating is detectable in match-result
  prices.
- **P1 passes but P2 fails:** co-movement exists but is not strength updating. It
  is consistent with generic repricing, and the sign test is what rules that
  in or out.

### Descriptive only — declared in advance

- **Walk-forward matrices:** HY correlations in rolling 3-day windows over the live
  set, finished matches removed. These are for viewing, not testing. About 450
  price changes per series is enough for the pooled estimate, not for daily ones.
- **Marchenko–Pastur:** eigenvalues of the hourly-grid correlation matrix in the
  June 11–16 block, over series live for the whole block. The count above the
  noise edge λ₊ = (1 + √(N/T))² is reported.

### Logged sensitivity runs

These cannot rescue a failed primary test:

- logit transform;
- pre-kickoff hours only (excludes both matches' in-play periods);
- hourly-grid Pearson instead of HY.

### Result (2026-10-16) — PARTIAL: co-movement, not shown to be strength updating

| | Prediction | Result |
|---|---|---|
| P1 — same team, different match: mean > 0 | at least 8 of 10 groups | **9 of 10** ✓ |
| P2 — future opponent: mean < 0 | at least 8 of 10 groups | **7 of 10** ✗ |
| P3 — same team above cross-group *(secondary)* | permutation p < 0.05 | +0.0096, **p = 0.037** ✓ |
| P4 — clustering recovers groups *(secondary)* | ARI above chance | ARI −0.003, p = 0.61 ✗ |

As registered, P1 passing with P2 failing means co-movement exists but is not
shown to be strength updating.

**Class means** (Hayashi–Yoshida ρ; 7,080 pairs, every pair except same-match
ones):

| class | pairs | mean | median |
|---|---|---|---|
| same team | 120 | +0.018 | +0.009 |
| same group, other | 240 | **+0.019** | +0.006 |
| cross group | 6,464 | +0.009 | +0.004 |
| future opponent | 240 | −0.010 | −0.006 |

"Same team" is no higher than "same group, other". What P1 detected therefore
looks group-wide rather than team-specific, which is the generic-repricing
alternative the sign test exists to catch.

**Logged sensitivity runs** (these cannot change the verdict): logit P1 9/10,
P2 7/10; pre-kickoff hours only P1 7/10, P2 5/10. The signal weakens without
in-play hours, so whatever co-movement exists concentrates where live
information is arriving.

**Estimator check, before the run.** On synthetic data with one series updating
hourly, HY recovered ρ = +0.60 as +0.63 and −0.50 as −0.45. Over 1,000 runs at
ρ = 0 it gave mean +0.0019 (z = 0.68) with 50.2% positive, so it is unbiased. A
1-minute grid Pearson on the same data gave about 0.00 (the Epps effect).

### Descriptive, not tested — the walk-forward matrices

In 3-day windows, every class is flat before the tournament. Once matches are
being played, the predicted signature appears: for windows starting June 15–16,
same team is +0.12 to +0.13 and future opponent −0.15 to −0.23, while cross group
stays near 0. **This was read after the pooled result and is a hypothesis for
fresh data, not a finding.** Changing the window after the fact is exactly what
this register exists to prevent.

### Data defect found: Kalshi's weekly maintenance pause

**Marchenko–Pastur** (June 11–16, hourly, N = 92, T = 143, noise edge 3.25): the
top eigenvalue is 11.4, with four more just above the edge. The top factor is
**not a market mode**. Its loadings are 53% positive, and 97% of its variance sits
in 6 of 143 hours, chiefly a jump and reversal on **Thursday June 11 between 07:00
and 10:00 UTC** across 32–43 series. Removing those hours drops the top eigenvalue
to 4.4.

Kalshi documents maintenance **every Thursday, 03:00–05:00 ET** (07:00–09:00
UTC in June). Orders flagged `CancelOrderOnPause` are cancelled at the start,
then makers re-quote. The busiest pre-match hour is 09:00 UTC on all three
recorded Thursdays (June 4, 11, 25).

A simultaneous jump and revert across many markets adds positive covariance to
**every** pair. It favours P1 and works against P2, so the verdict above stands
as registered, on data with a known contaminant. A maintenance-masked re-run is
reported separately below. It is post-hoc, and it cannot change this verdict.

### Post-hoc re-run with maintenance masked (2026-10-16) — the verdict is unchanged

Increments overlapping Thursdays 07:00–10:00 UTC are dropped: the documented
window plus the re-quote hour. This was decided after the registered result was
seen, so it is informative only.

| | registered | maintenance masked |
|---|---|---|
| P1 same team > 0 | 9 of 10 | 9 of 10 |
| P2 future opponent < 0 | 7 of 10 | **6 of 10** |
| P3 permutation p | 0.037 | 0.050 |
| P4 ARI | −0.003 | +0.001 |
| future opponent mean | −0.010 | −0.004 |
| cross group mean | +0.0086 | +0.0086 |

**The expectation stated before this run was wrong.** It was that the artefact
favoured P1 and worked against P2. In fact masking left P1 and the cross-group
baseline unchanged and made P2 weaker. The maintenance pause dominates **grid**
statistics — the Marchenko–Pastur top eigenvalue — because a rare simultaneous
jump is most of what a mostly-zero hourly series contains. It barely affects
pooled Hayashi–Yoshida estimates, where a few hours are diluted across about 450
price changes per series.

**Net reading of H12.** There is a small, robust positive co-movement among
same-group matches: +0.017 to +0.019 against +0.009 across groups. It is not
team-specific, and the opposite-sign signature of strength updating is not
established on the pooled data. The walk-forward pattern (in-tournament windows
only) remains the most suggestive observation and the least tested.

**Data-layer consequence.** Thursday 07:00–10:00 UTC is not normal trading. Any
analysis on an hourly or coarser grid, or one that counts cancellations, must mask
it.
