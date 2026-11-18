# Guide 1 — Event-driven bars

*Stage 2, step 1. Read sections 1–3 before writing any code.*

---

## 1. Theory

### 1.1 The problem with the clock on the wall

Almost all market data gets chopped into **time bars**: one row per minute, with the
open, high, low and close inside it. The trouble is that markets don't produce
information at a constant rate. Here are two minutes of a World Cup match:

- minute 12, nothing happening: 40 trades, mid unchanged;
- minute 13, a goal: 2,000 trades, the mid jumps 30¢.

A time bar treats these as the same kind of observation. Statistically they aren't.
One is a calm draw from a small-variance distribution; the other is a draw from a
huge-variance one.

### 1.2 Why that produces fat tails in returns: the mixture of normals

"Returns" here means each bar's price change (close minus open). "Fat tails" means
that, across all bars, extreme price changes turn up far more often than a normal
distribution would predict.

Suppose each trade moves the price by a random amount with standard deviation 1,
independently. Then a bar holding *n* trades has a price change with standard
deviation √n. (Variances add: n trades give variance n.)

Now suppose half the hours are quiet (4 trades, so sd = 2) and half are busy
(64 trades, so sd = 8). Hourly returns are a **mixture** of two normal distributions,
one narrow and one wide. Kurtosis measures how fat the tails are (it's 3 for a normal
distribution). For a mixture with zero mean:

  kurtosis = 3 · E[σ⁴] / (E[σ²])²

- E[σ²] = (4 + 64) / 2 = 34
- E[σ⁴] = (2⁴ + 8⁴) / 2 = (16 + 4,096) / 2 = 2,056
- kurtosis = 3 × 2,056 / 34² = 3 × 2,056 / 1,156 ≈ **5.3**

So you get fat tails, even though every individual trade was perfectly normal. The fat
tails come entirely from the **clock**, from mixing busy and quiet periods.

Now sample a bar every 16 trades instead. Every bar has the same variance, 16, so
there's no mixture, and by the central limit theorem the returns are close to normal
(kurtosis ≈ 3).

That's the core idea. Peter Clark showed it in 1973: returns look much closer to
normal if you measure time in *trades or volume* rather than minutes. Markets run on
an **information clock**, and bars should follow it.

### 1.3 The bar types

| Bar | A new bar closes when… | Weight per trade |
|---|---|---|
| Time | a fixed amount of clock time passes | — |
| Tick | N trades have happened | 1 |
| Volume | N contracts have traded | trade size |
| Dollar | $N has changed hands | size × price |

AFML (sections 2.3.1.1–2.3.1.4) argues that volume and dollar bars beat tick bars.
One large order can print as many small trades, so the trade count is partly an
accident of how orders were split. Dollar bars additionally adjust for the price
level. Information-driven bars (imbalance and run bars, section 2.3.2) go a step
further, but leave those for later.

### 1.4 Why it matters for what comes next

- **Statistics.** Most methods assume observations are roughly IID (independent and
  identically distributed). Bars that each carry about the same amount of
  information get closer to that.
  
  **The limit: jumps.** Activity bars even out differences in *how much trading*
  happens. They don't remove *jumps*. When a goal is scored, the price reprices almost
  at once, mostly by makers pulling and re-posting quotes, before much volume trades.
  So one bar can contain a 20–30¢ move on an ordinary amount of volume. Bars that
  contain a jump still have far higher variance than the rest, and the returns stay
  fat-tailed even on a perfect volume clock. This is why separating jumps (a later
  step: detect them, then study the calm periods and the jumps separately) goes with
  activity bars rather than replacing them. On Kalshi, expect activity bars to
  *reduce* kurtosis compared with time bars, not to bring it down to 3.
- **Features and labels.** Later features and labels are computed per bar. On the
  wrong clock, "the last 10 bars" means two hours of nothing in one place and two
  seconds of chaos in another.
- **Honest comparison.** Comparing a busy match with a quiet one is fairer per unit
  of activity than per minute.

### 1.5 Worked example by hand

Eight trades over three minutes, with the mid after each trade:

| # | time | size | mid after |
|---|---|---|---|
| 1 | 00:05 | 2 | 50 |
| 2 | 00:20 | 1 | 50 |
| 3 | 00:50 | 3 | 51 |
| 4 | 01:10 | 1 | 51 |
| 5 | 02:02 | 4 | 52 |
| 6 | 02:03 | 2 | 53 |
| 7 | 02:04 | 1 | 53 |
| 8 | 02:30 | 2 | 52 |

**One-minute time bars:**

| bar | trades | volume | close mid |
|---|---|---|---|
| minute 1 | 1–3 | 6 | 51 |
| minute 2 | 4 | 1 | 51 |
| minute 3 | 5–8 | 9 | 52 |

The volumes are 6, 1 and 9, which is very uneven.

**Volume bars with a threshold of 4 contracts.** Accumulate the sizes; when the running
total reaches 4 or more, close the bar and reset to 0:

- trades 1–3: running total 2 → 3 → 6 ≥ 4, so close bar A (volume 6, close 51);
- trades 4–5: 1 → 5 ≥ 4, so close bar B (volume 5, close 52);
- trades 6–8: 2 → 3 → 5 ≥ 4, so close bar C (volume 5, close 52).

The volumes are 6, 5 and 5, which is roughly even. Bar B stretches across the quiet
minute, and the burst at 02:02–02:04 gets split, because that's where the activity is.

*Check yourself:* with a threshold of 3, the bars are trades {1,2}, {3}, {4,5}, {6,7}
and {8 is left over}. Make sure you get the same before moving on. Your code's
known-answer test is exactly this.

**The convention used here:** when one trade pushes the total past the threshold, it
closes one bar, and the overflow is *not* carried forward (reset to 0). Other
conventions exist. Pick one, write it down, and test it.

---

## 2. What changes on Kalshi

1. **Fractional contracts.** Trade sizes like 156.06 exist. Across the pool's in-play
   trades, about 80% are non-integer: in every leg between 60% and 89%, out of 3.17
   million trades. This comes straight from Kalshi's feed, not from our processing:
   the raw messages carry sizes in fixed-point fields such as
   `last_trade_size_fp: "199.80"`. It's fine for volume bars. Just don't assume
   integers anywhere, for example in `==` comparisons on sizes.
2. **Which "dollar" to use.** Every Kalshi trade is a YES buyer at price P against a
   NO buyer at 1 − P. "Dollars exchanged" depends on which side you count. At 3¢ the
   YES side pays almost nothing while the NO side pays almost a dollar, so AFML's
   dollar bars are ambiguous here. Start with **volume bars** in contracts. Section 7
   has an exercise on a weighting designed for binary contracts.
3. **The mid barely moves.** In 26JUN14GERCUW's TIE leg, in play, there are about
   28,000 trades but only about 600 mid changes. Small bars therefore have mostly zero
   returns, and on a 1¢ grid normality tests are dominated by those zeros. Keep bars
   large enough that each one averages a few mid changes. Always report the
   *fraction of zero-return bars* next to any normality statistic.
4. **Use one of the probability transforms.** As in H12, measure returns as changes
   in 2·asin(√(mid/100)), so a 1¢ move at 3¢ and at 50¢ are comparable.
5. **The collapse at settlement.** In the last seconds before the recorded end, the
   book can collapse to something like 1/99. Drop book states with a spread of 50¢ or
   more before taking mids.
6. **Start in play.** `load_book` and `load_trades` default to `inplay_only=True`,
   which is about two hours per match. Pre-match trading lasts days, crosses the
   recording gap and the Thursday maintenance windows, and is a separate later step.
7. **The threshold must not look ahead, later.** For this descriptive study, choosing
   the threshold from the match's own total volume is fine: you're *describing* the
   match. Once bars feed features for prediction, the threshold must come only from
   the past, for example the average volume of earlier matches or a trailing
   estimate. Note in your code which one you used.
8. **Same-millisecond ordering.** Book rows and trades can share a timestamp. Take a
   bar's closing mid as the last book state with `ts` ≤ the bar's last trade `ts`.
   That can include a book update from the same millisecond that came just after the
   trade. It's acceptable for description; just be aware of it.

---

## 3. Data you'll use

Use the `_v2` tree; it's required.

```python
import sys; sys.path.insert(0, 'pipeline')
import canon_load as cl          # run with:  CANON_SUFFIX=_v2 jupyter lab   (or python3 ...)

pool  = cl.split_matches('cv')        # 21 matches. Never use 'sealed'.
legs  = cl.legs('26JUN14GERCUW')      # ['CUW', 'GER', 'TIE']
trades = cl.load_trades(m, leg)       # ts, price, size, aggressor ('buy' / 'sell')
book   = cl.load_book(m, leg, columns=['bid', 'ask'])   # ts, bid, ask   (+ many more columns available)
```

`ts` is integer milliseconds UTC. Prices are in cents.

---

## 4. Script or notebook

| Part | Where | Why |
|---|---|---|
| Bar-building functions and their known-answer test | `pipeline/bars.py` (script) | Reused by every later stage; must be tested |
| Looking at one match, plots, comparing clocks | `notebooks/bars_explore.ipynb` | Exploration |
| Building bars for all 21 matches and the summary table | `pipeline/bars.py run` (script) | Writes files; must be reproducible |

Outputs go to `canonical/mine/bars/`.

---

## 5. Steps

### Step 1: Load one leg and know your numbers *(notebook)*
Load trades and book for **26JUN14GERCUW, TIE**, in play.

**Checkpoint:**
- 27,998 trades;
- 7,832,789 contracts in total (round the sum);
- 39,931 book rows;
- about 600 changes in the mid;
- median mid about 4.5¢.

If you get these, your loading is right. If not, stop and ask.

### Step 2: The bar-assignment function *(in `bars.py`)*
`bar_ids(weights, threshold) -> np.ndarray of int`

- Input: one weight per trade, in time order (1 for tick bars, size for volume bars),
  and a threshold.
- Output: for each trade, the number of the bar it belongs to: 0, 0, 0, 1, 1, …
- Logic: walk through the trades keeping a running total. Add the weight; give the
  trade the current bar number; if the total is now ≥ threshold, increase the bar
  number and reset the total to 0.
- A plain Python loop is fine here: about 28,000 trades per leg.

### Step 3: The known-answer test, before anything else *(in `bars.py`, run as `python3 pipeline/bars.py selftest`)*
Assert all of the following:
1. `bar_ids([2,1,3,1,4,2,1,2], 4)` → `[0,0,0,1,1,2,2,2]` (the worked example).
2. With threshold 3 → `[0,0,1,2,2,3,3,4]`. The last bar (4) is incomplete.
3. One huge trade: `bar_ids([1, 10, 1], 4)` → `[0,0,1]`. The 10 closes bar 0 and
   doesn't create several bars, by the reset convention.
4. All weights equal to 1 with threshold 5 on 12 trades → bars of exactly 5, 5 and 2
   trades.

Print PASS or FAIL for each one.

### Step 4: Turn bar numbers into bars *(in `bars.py`)*
`aggregate(trades, ids, book) -> DataFrame`, one row per bar, with columns:

`ts_open, ts_close, n_trades, volume, buy_vol, sell_vol, vwap, mid_open, mid_high, mid_low, mid_close`

- Trade columns: group the trades by `ids`. `buy_vol` is the volume where
  `aggressor == 'buy'`. `vwap` is Σ(price × size) / Σ size.
- Mid columns: first drop book rows with ask − bid ≥ 50 and compute mid = (bid + ask)/2.
  Then:
  - `mid_close` is the last mid at or before `ts_close`. Use `pd.merge_asof` with
    `direction='backward'`.
  - `mid_open` is the previous bar's close. For the first bar, use the mid at or
    before `ts_open`.
  - high and low come from the book mids inside the bar, including the open.
- **Drop the last bar if it didn't reach the threshold.**

`time_bars(trades, book, freq='1min')` builds the same columns, grouping by
`ts // 60_000` instead of by `ids`. Reuse `aggregate`.

### Step 5: Build three clocks for the leg *(notebook)*
- Time bars: 1 minute.
- Tick bars: threshold = number of trades / (number of 1-minute bars).
- Volume bars: threshold = total volume / (number of 1-minute bars).

That gives each clock roughly the same number of bars, so the comparison is fair.
Here "number of 1-minute bars" means minutes with at least one trade.

**Checkpoint:**
- **117 time bars.** The trades span about 120 minutes, but 3–4 minutes have no trades
  at all, and a minute with no trades makes no bar here. That's a choice: you could
  instead emit empty bars that carry the last mid forward. Write down which you
  chose.
- **116–117 tick bars.**
- **About 110–111 volume bars, noticeably fewer.** That's expected, not a bug. Under
  the reset convention, a large trade that overshoots the threshold throws its excess
  away, so each volume bar holds a bit more than the threshold on average. Check it:
  mean volume per bar should be above the threshold. To match the counts exactly,
  you could lower the threshold slightly, or switch to carrying the overflow forward.
  Either is fine as long as you note it.
- each clock's summed volume equals the total from Step 1, minus the dropped
  incomplete bar;
- `ts_close` strictly increasing in every table.

### Step 6: Compare the clocks *(notebook)*
For each clock, compute the returns r = Δ 2·asin(√(mid_close/100)), then:

1. **Where the bars fall in time.** Count bars per 10-minute window and plot all three
   clocks. Time bars are flat by construction; tick and volume bars should pile up
   around events. Check that the spikes line up with big mid moves (goals).
2. **Fraction of zero returns.** Report this first; it qualifies everything below.
3. **Kurtosis** (`scipy.stats.kurtosis(r, fisher=False)`, which is 3 for a normal
   distribution) and the **Jarque–Bera statistic** (`scipy.stats.jarque_bera`).
4. **Lag-1 autocorrelation** of r.
5. **Variance stability.** Split the bars into 4 consecutive blocks, take the variance
   of r in each, then take the variance of those 4 variances. Smaller is more stable.

**What to expect, and why you shouldn't force it:** the theory predicts that volume and
tick bars give lower kurtosis and more stable variance than time bars. Kurtosis will stay
well above 3 on every clock, because goals are jumps (section 1.4). To see how much of
it the jumps cause, recompute kurtosis after dropping the 2–3 bars with the largest
|r| and compare the clocks again. On one match with about 120 bars, the numbers will
be noisy. Write down what you actually see,
including if time bars win. One leg is an illustration, not a result.

### Step 7: All 21 pool matches *(script: `python3 pipeline/bars.py run`)*
For every pool match and leg, and each of the three clocks:
- save the bars to `canonical/mine/bars/{match}_{leg}_{clock}.parquet`;
- add a row to a summary table: match, leg, clock, n_bars, zero fraction, kurtosis,
  JB, lag-1 autocorrelation, variance of variances.

Save the summary to `canonical/mine/bars/summary.csv`.

**Checkpoints:**
- 21 matches × 3 legs × 3 clocks = 189 bar files;
- a volume-conservation assertion passes for every file;
- no sealed match appears (the loader would raise anyway).

Then, in the notebook: for each statistic, compute the median across matches, by clock
and by leg type (favourite, underdog, tie, where the favourite is the team leg with
the higher median mid). **Count how many of the 21 matches** have volume bars beating
time bars on kurtosis. As always, a count across independent units says more than a
pooled average.

---

## 6. Common mistakes

- **Using `ts` of the book row that triggered nothing.** Bar times come from trades;
  the book only supplies mids.
- **Forgetting to drop the incomplete last bar.** It has less volume than the others
  and distorts the variance.
- **Measuring returns in raw cents.** A 1¢ move at 3¢ is 33%, but at 50¢ it's 2%. Use
  the transform.
- **Comparing clocks with very different bar counts.** More bars means smaller bars
  and different statistics. Keep the counts close (Step 5) and report them next to
  every statistic.
- **A cartesian merge.** If `merge_asof` or `merge` produces more rows than you put
  in, something is duplicated. Check row counts after every join.
- **Pooling across matches before looking per match.** Always look match by match
  first.

---

## 7. Exercises, after Step 7

Write down what you try in `canonical/mine/variants.md`.

1. **A match-level clock.** Build volume bars from the *sum* of all three legs'
   volume, so all three legs share the same bar boundaries. That's what you'd want
   for cross-leg features. How different are the boundaries from per-leg bars?
2. **Bars weighted for binary contracts.** Use weight = size × P(1 − P), with P as a
   fraction. P(1 − P) is the variance of one contract's payoff (a Bernoulli
   variable), and it's symmetric between YES and NO, which fixes the dollar-bar
   ambiguity from section 2. It's also exactly what Kalshi's fee is proportional to.
   Does it beat plain volume bars on the Step 6 statistics?
3. **Bar size.** Repeat Step 6 with 2× and ½× the threshold. Which conclusions survive?
4. **Pre-match.** Load one match with `inplay_only=False` and look at bars across the
   whole market life. Mask the June 16–22 recording gap and Thursday 07:00–10:00 UTC.
   How many volume bars fall in the last hour before kickoff, compared with the
   previous three days?

---

## 8. Reading

- **AFML, chapter 2, sections 2.1–2.3**: standard and information-driven bars.
  Exercise 2.1 is essentially Steps 6–7 on futures data.
- **Clark, P. (1973)**, "A Subordinated Stochastic Process Model with Finite Variance
  for Speculative Prices," *Econometrica*: the original mixture-of-normals idea.
- **Ané, T. & Geman, H. (2000)**, "Order Flow, Transaction Clock, and Normality of
  Asset Returns," *Journal of Finance*: the empirical case for the trade clock.
- **Easley, López de Prado & O'Hara (2012)**, "The Volume Clock: Insights into the
  High-Frequency Paradigm," *Journal of Portfolio Management*: why volume time
  matters for modern markets.

When you finish Step 3, send me `bars.py` and the self-test output. When you finish
Step 7, send me `summary.csv` and I'll check it.
