# Guide 0 — Volatility, from the ground up

*Background reading for guide 1 (bars) and the later jump work. No code; there are
small exercises at the end.*

---

## 1. The one-sentence definition

**Volatility is how big price changes typically are**, measured as the standard
deviation of returns over some period.

That's all. It's about the *size* of moves, and it ignores:
- **direction**: up or down doesn't matter;
- **whether moves reverse**: that's autocorrelation, a separate property;
- **the price level**: a 50¢ contract isn't more volatile than a 5¢ one just because
  it's higher.

**Example.** Two contracts both start at 40¢ and both end the day at 40¢.
- A moves 40, 41, 40, 39, 40: changes of +1, −1, −1, +1.
- B moves 40, 55, 30, 50, 40: changes of +15, −25, +20, −10.

Same start, same end, same net change of zero. B is far more volatile, because its
*typical move* is about 18¢ against about 1¢ for A. Volatility measures the journey,
not where it ends up.

---

## 2. How you compute it

### 2.1 Returns first
Volatility is always computed on **returns** (changes), never on price levels. For a
series of prices p₀, p₁, …, pₙ, the returns are rₜ = pₜ − pₜ₋₁. Use percentage or log
changes for stocks, and transformed changes for binary contracts (section 7).

### 2.2 Worked example
Five one-minute changes of a mid, in cents: **+1, −2, +3, 0, −2**.

- Mean: (1 − 2 + 3 + 0 − 2) / 5 = 0
- Squared deviations from the mean: 1, 4, 9, 0, 4, which sum to **18**
- Sample variance: 18 / (5 − 1) = 4.5
- **Sample standard deviation: √4.5 ≈ 2.1¢ per minute**

So the typical one-minute move is about 2¢.

### 2.3 Realised variance: the version used at high frequency
Over short intervals the average return is tiny compared with the moves themselves,
so practitioners usually drop the mean and just add up squared returns:

  **realised variance (RV) = Σ rₜ²**  realised volatility = √RV

For the example, RV = 1 + 4 + 9 + 0 + 4 = 18 (cents²), so realised volatility over the
5 minutes is √18 ≈ 4.2¢. That's a *total over the window*, not a per-minute figure.
Per minute it's √(18/5) ≈ 1.9¢.

**Pay attention to the units.** Volatility is always "per something": per minute, per
bar, per day, per year. A number without its time unit is meaningless.

---

## 3. How volatility scales with time: the square-root rule

If returns are **independent** from one period to the next, *variances add up*. Over
n periods:

  Var(total) = n · Var(one period)  →  sd(total) = √n · sd(one period)

**Example.** A stock with 1% daily volatility has about 1% × √252 ≈ **15.9% annual**
volatility (252 trading days). It is *not* 252%, because ups and downs partly cancel.

**The catch:** this rule only holds if returns are independent, that is, a random
walk. If moves tend to reverse (mean reversion), long-horizon volatility is *smaller*
than √n predicts. If they tend to continue (trend), it's *larger*. The variance ratio
from our last conversation is exactly a test of whether the √n rule holds.

---

## 4. The different "volatilities" people talk about

The word covers several different things, which is a common source of confusion.

| Name | What it is | Looking | Example |
|---|---|---|---|
| **Realised / historical** | Measured from past returns, as in section 2 | Backward | "This leg moved about 2¢ per minute over the last hour" |
| **Implied** | The volatility that makes an *option's* market price fit a pricing model | Forward: the market's expectation | VIX ≈ 20 means the market expects about 20% annualised S&P volatility |
| **Instantaneous / stochastic** | Volatility treated as a hidden quantity that changes over time; you never observe it directly, only estimate it | Now | Heston-type models; the σ̂ in jump tests |
| **Local** (Dupire) | A *pricing model* where volatility is a fixed function σ(S, t) of price and time, fitted so the model matches all option prices | Model | Used to price exotic options |

**For us, only the first and third matter.** "Recent local volatility" in the jump
discussion meant a *recent estimate of realised volatility*, local in time, not
Dupire's model. Kalshi has no options, so implied volatility doesn't exist here in the
usual sense. Section 7 shows that a binary contract's price tells you something
similar anyway.

---

## 5. Volatility is not constant: clustering

In every market, big moves cluster together and quiet periods cluster together. A
volatile hour tends to be followed by another volatile hour. This is **volatility
clustering**, and it's why a single number for "the volatility" of a market is
misleading.

**Estimating volatility that changes over time:**

- **Rolling window:** the standard deviation of the last K returns. A small K reacts
  fast but is noisy; a large K is smooth but slow to notice change.
- **Exponentially weighted (EWMA):** recent returns count more.

    σ²ₜ = λ · σ²ₜ₋₁ + (1 − λ) · r²ₜ₋₁

  λ close to 1 (RiskMetrics used 0.94 for daily data) means a long memory. One line of
  code, no window to store.
- **GARCH models** formalise clustering: today's variance depends on yesterday's
  variance and yesterday's squared return. EWMA is a special case.

**On Kalshi:** volatility in a match leg is extremely uneven. It's near zero for long
stretches, enormous at a goal, and different before kickoff, in play, and in the final
minutes. Any "volatility" number you compute should state *which window* and *which
phase* it covers.

> **Parked idea (Aniruddh, 24 Oct):** when we start measuring volatility by phase,
> consider letting the data *find* the phases rather than fixing them by the clock:
> an unsupervised classifier of market regimes. This connects to H6 (within-match
> regimes exist and are learnable).

---

## 6. What volatility is NOT

| It's not… | Why | The right concept |
|---|---|---|
| Direction | A price can rise steadily with almost no volatility | Drift / expected return |
| Bouncing back | A jump that stays put is volatile too | Autocorrelation, mean reversion |
| The whole story of risk | Two series with equal volatility can have very different worst cases | Tails (kurtosis), jumps |
| Always "real" | At tick frequency, trade prices bounce between bid and ask with no change in value | Microstructure noise |

### 6.1 Measured volatility depends on how often you sample
Bid-ask bounce adds fake, reverting movement at the tick level. Realised volatility
from trade prices therefore *grows* as you sample more often. With 10,000 trades
bouncing 45 → 46 → 45 → 46, realised variance from trades is 10,000 × 1² while the
true value hasn't moved at all.

Plotting realised volatility against sampling interval shows this; it's called a
**volatility signature plot**. The fixes are to use mids instead of trade prices, to
sample more coarsely, or to use bars. That's one reason guide 1 exists.

### 6.2 Jumps sit inside realised variance
Realised variance = (continuous part) + (sum of squared jumps). One goal can supply
most of a match's realised variance. Example: 100 calm one-minute moves of about 0.5¢
give RV ≈ 100 × 0.25 = 25. One goal moving 25¢ adds 625 on its own, **25 times the
whole calm part**. That's why the jump tests use *bipower variation*, which estimates
the continuous part while largely ignoring jumps. The gap between RV and bipower
variation is the jump part.

---

## 7. Volatility in binary contracts: what's special

This is where intuition from stocks breaks, so it's worth understanding properly.

### 7.1 The price is bounded and must end at 0 or 100
A Kalshi contract settles at exactly 0 or 1 (0¢ or 100¢). Its price lives between them
and *must* end at one of them.

### 7.2 Total remaining volatility is fixed by the price
If the price is fair (it behaves like a *martingale*, so today's price is the best
guess of the final value), there's an exact result:

  **expected total remaining realised variance, from now until settlement, = p(1 − p)**

(with p as a fraction).

*Why.* The final value p_T is 0 or 1, so p_T² = p_T. Then

  E[(p_T − p_t)²] = E[p_T²] − 2 p_t E[p_T] + p_t² = p_t − 2p_t² + p_t² = p_t(1 − p_t)

For a martingale, the increments are uncorrelated, so the expected sum of squared
increments equals that total.

**What it means:**

| Price now | Remaining variance p(1 − p) | Total remaining "volatility" √ |
|---|---|---|
| 50¢ | 0.25 | 50¢ |
| 20¢ | 0.16 | 40¢ |
| 5¢ | 0.0475 | ≈ 22¢ |
| 1¢ | 0.0099 | ≈ 10¢ |

A contract at 50¢ has the most uncertainty left to resolve. One at 5¢ has much less,
because it will most likely just drift to 0. Every contract has a fixed **budget of
variance** to spend before settlement, set by its current price. A goal spends a big
chunk of it at once.

**p(1 − p) is the variance of a Bernoulli random variable.** That's no coincidence.
The final payoff is a Bernoulli(p) draw (1 with probability p, else 0), so its
variance, given what's known now, is p(1 − p). The result above says the price path
has to "spend" exactly that much variance on the way to settlement: today's
uncertainty about the final payoff equals the expected amount of movement still to
come.

This is the binary-contract analogue of implied volatility: the price itself tells you
how much movement is still to come. (It also explains why Kalshi's fee is
proportional to p(1 − p): it's charging in proportion to uncertainty.)

### 7.3 Why a 1¢ move means different things at different prices
Near 50¢ the price has room to move both ways; near 5¢ it's squeezed against the
floor. So raw cent changes aren't comparable across price levels. The **arcsine
transform**, 2·asin(√p), stretches the ends so that a "unit" of movement means roughly
the same everywhere. It's the variance-stabilising transform for a probability.

| Move | Raw change | Arcsine change |
|---|---|---|
| 45¢ → 46¢ | 1¢ | 0.020 |
| 5¢ → 6¢ | 1¢ | 0.044 |
| 45¢ → 72¢ (a goal) | 27¢ | 0.556 |

On the transformed scale, a 1¢ move at 5¢ counts about twice as much as one at 45¢.
That's why every volatility calculation in this project uses the transformed mid.

### 7.4 Time matters too
As the match goes on, less can happen. A team leading 2–0 in the 85th minute is priced
near 97¢, its remaining variance is small, and its volatility is low because the
outcome is nearly decided. In a tied match in the 85th minute, the TIE leg is high and
one goal would move everything violently. Volatility depends on the price, the clock
and the score together.

---

## 8. Three Kalshi examples side by side

| Situation | Mids (¢) | What volatility sees |
|---|---|---|
| Calm pre-match | 45, 45, 46, 45, 45 | Tiny: two 1¢ moves, RV in arcsine units ≈ 2 × 0.02² ≈ 0.0008 |
| A goal | 45 → 72, then 72, 71, 72 | Huge, dominated by one move: 0.556² ≈ 0.31 from the jump alone |
| Bid-ask bounce | Trades at 45, 46, 45, 46; mid steady at 45.5 | Trades show volatility; **the mid shows none**. The movement is fake |

---

## 9. Why volatility matters for what we're building

- **Spreads.** A market maker's spread should widen when volatility is high: more
  risk of being run over (adverse selection) and more inventory risk. The volatility
  that matters is **instantaneous and forward-looking**: how much the price is likely
  to move *while your quote rests and while you hold the position*. A long historical
  average is the wrong input. You estimate the forward-looking number from *recent*
  realised volatility (a short window or EWMA), plus anything known about what's
  coming. Realised volatility lags: after a quiet spell it's low right before a goal.
  So known risk, such as being in play, the match minute or a tied score late on,
  should widen quotes before any movement shows up.
- **Labels (Stage 3).** AFML sets profit and stop barriers as *multiples of recent
  volatility*. A fixed 2¢ barrier means very different things at a goal and in a
  quiet spell.
- **Features.** Many features are normalised by recent volatility so they're
  comparable across calm and busy times.
- **Jump detection.** A move counts as a jump relative to *recent* volatility.
- **Position sizing.** Kelly-style sizing divides edge by variance.

---

## 10. Summary

- Volatility is the typical *size* of moves: the standard deviation of returns,
  always *per unit of time*.
- It says nothing about direction or reversal. Those are drift and autocorrelation.
- It scales with √time only if returns are independent.
- It changes over time and clusters. Estimate it locally (rolling or EWMA) and state
  the window.
- Tick-level noise inflates it; jumps can dominate it.
- For binary contracts, the remaining variance is set by the price, p(1 − p), and
  should be measured on the arcsine scale.

---

## 11. Check yourself

1. A leg moves +2, −2, +2, −2 (cents). Another moves +2, +2, +2, +2. Which is more
   volatile? Which has moved further? *(Equal volatility; the second has moved 8¢,
   the first 0.)*
2. Volatility is 3¢ per minute and returns are independent. Roughly what is it per
   hour? *(3 × √60 ≈ 23¢.)*
3. A contract is at 10¢. What's the expected total remaining realised variance until
   settlement, as a fraction? *(0.1 × 0.9 = 0.09.)*
4. Why does realised volatility from *trade prices* keep rising as you sample every
   second, then every 100 ms, then every trade? *(Bid-ask bounce: fake reverting
   moves add squared returns without any change in value.)*

## 12. Reading

- **Hull, *Options, Futures, and Other Derivatives*,** the chapter on estimating
  volatilities and correlations (EWMA, GARCH): clear and practical.
- **Andersen, Bollerslev, Diebold & Labys (2003),** "Modeling and Forecasting Realized
  Volatility," *Econometrica*: the standard reference for realised volatility.
- **Aït-Sahalia & Jacod, *High-Frequency Financial Econometrics* (2014):** advanced;
  microstructure noise and jumps, for later.
