> **Appendix to `BLUEPRINT.md`, not a competing plan.** The blueprint holds the gates and the current state;
> this file holds the closed record of each candidate and why it closed. When the two disagree, the blueprint
> wins. Written 2026-09-04 at the user's request, superseding nothing.

# Candidate yields — the ledger

Four ways of being paid to hold a position were collected and measured against one yardstick. Three are
closed. One is one measurement from closing.

---

## The finding that explains all four at once

**None of the four is a market price. All four are administered numbers.**

| candidate | what sets the number we can access |
|---|---|
| #1 perp funding | the venue default — median exactly `5e-05` on **all six venues** |
| #2 dated basis | quoted at a few bps; the round trip is not |
| #3 stablecoin lending | a posted rate — kucoin shows **one distinct value in 3,201 observations** |
| #4 stable-pair LP | a fee tier fixed by the pool |

**We are not being paid for taking risk. We are being paid the venue's advertised number, and the venue's own
costs are calibrated to consume it.** Four candidates arriving at zero is not four coincidences — it is one
structure seen four times.

A direct consequence, already measured: **funding rests at its default on every venue we checked, so adding
venues does not enlarge the opportunity set.** The expansion plan is closed by measurement, not opinion.

---

## #1 — Perp funding carry · ≈ zero, one measurement from a verdict

**The number's history is the whole story:**

| stage | figure | what was wrong with it |
|---|---|---|
| August, modelled | 10–54% APR, top 55–58% | mean of a **right-skewed** funding series |
| 21.5 h window | cost ÷ income 27% | positions had **not yet paid an exit** |
| 8.9 d window, funding only | **16.4% net APR** | **only one of the trade's two legs was booked** |
| both legs booked, marked to market | **−$0.46** | — |

Whole run, 46 positions: funding **−$2.27**, basis **+$0.75**, total **−$1.53** — the opposite composition to
the story told in August.

**Why it went unnoticed for weeks.** `close_price` was NULL on every closed leg, so `paper_pnl` was defined as
income minus costs with no price term. The reconciliation "tied to the cent" — residual `0.00000000` across 92
legs — because **it was an identity and could not fail.** It looked like verification and was not.

**Corrected constants:** realised ÷ modelled **0.85** (not 0.50), stable and non-drifting · cost ÷ income
**87.9%** on complete round trips (not 27%) · **break-even ≈ 3.5 days**, and nothing held under it has ever
made money.

**Two defects fixed on 2026-09-04, both unproven:**
- `max_basis_bps` tested a lookback mean, so gate/POWER_USDT was admitted while its basis had sat at −1250 bps
  for an hour. That one $27 position **decided the sign of the whole window** (−$2.42 of −$2.26; the other
  four net +$0.16). It now tests the current basis and would be rejected 6.6× over.
- R4's exit floor was interval-blind: the `5e-05` default annualises to **10.95% at 4 h but 5.48% at 8 h**
  against a flat 8% floor, so **an 8 h name that merely reverted to the default was exited by construction** —
  5 of 14 round trips, all Gate. Now one per-epoch rate; admitted set 227 → 477.

**Structural constraints that no fix addresses:**
- **0 of 212 selection cycles ever had the 15 passing names** the concentration work requires for a p95 death
  not to be a portfolio event.
- Death rate ~12%/yr overall, **24.3% on MEXC** against 11.3% elsewhere.
- **Every risk control costs more than the edge it protects**: weight cap at 6.7% −5.9 pp · venue cap at 40%
  ~−8 pp to buy 0.5 pp of expected value · the funding-collapse exit destroyed $6.16 of $6.85 in-window.
  **The necessary counter-argument: this window has zero deaths and 2.5% negative-carry epochs, so it contains
  none of the events those controls exist for.** Insurance looks overpriced in a year with no fire — which is
  precisely why the durability gate cannot be closed inside one regime.

**Status: awaiting the first window with a live-recorded basis leg, 11–12 September.**

---

## #2 — Dated basis · CLOSED, negative, replicated

Held to settlement the trade earns the quoted basis. **The quoted basis is tiny and the round trip is not.**

| horizon | median basis | cost (held) | median net | clears cost |
|---|---|---|---|---|
| 4 d | +3.2 | 17.6 | **−14.4** | 7/25 |
| 3 d | +1.0 | 12.5 | **−10.4** | 3/25 |
| 24 h | +1.5 | 12.0 | **−7.4** | 5/25 |
| 12 h | −2.4 | 7.3 | **−10.9** | 0/25 |
| 1 h | −3.9 | 5.5 | **−10.2** | 1/25 |

All bps. Cost is full spot + half future — the future settles rather than being traded out.

**Fee sensitivity ends it**, over three crossings: 0 fees → 7/25 · 1 → 5/25 · 2 → 2/25 · **3+ → 0/25.**
Every zero-fee survivor is BTC or ETH; **no alt survived on any venue on either date.** Their nets are
0.8–7.3 bps — smaller than any realistic fee schedule.

**Replicated on 2026-09-04** in sign, magnitude and composition (median net −5.3 to −11.1 bps; 6 of 22 clear
cost at 4 d). **And costs were 2.3× lower that cycle — 7.5 bps against 17.6 — and the sign still did not
turn.** The most favourable variable moved sharply the favourable way and the answer did not change.

**Orthogonality could not be tested and was correctly refused**: 14 of 15 shared coins sit at the universal
funding rest value, so a correlation against a near-constant proves nothing. Untestable until funding leaves
its default — **the same regime dependence that binds #1.**

---

## #3 — Stablecoin lending · CLOSED, the premium is a currency illusion

Only **6 of 14** collected series are earnable; the rest are borrow rates. Four of the six are constants. All
cluster **3.35–3.65%**, all flexible-term.

**The high rate and the closed exit are the same event.** Aave v3: time-weighted mean **5.187%** vs median
**3.351%**. The gap is **20.4% of observations in a recurring 01:00–05:00Z window at 99.6% utilisation paying
~12%** — and in that window free liquidity collapses from **$188M to $0.89M, 210×**, against $2.12bn borrowed.
Same pool, every night. The 12% is visible and not withdrawable.

*Note the asymmetry with #1: the median is the honest estimator in both cases, but for opposite reasons —
there because the series is skewed, here because the tail is unreachable. "Use the median" is a conclusion,
not a rule.*

**Cost is not collected** — gas ×2 on Ethereum for Aave, withdrawal fee plus EUR on/off-ramp for the CEX
venues. **Declared unmeasured, not estimated.**

**Risk named, not priced:** Aave v3 = contract risk (smart contract, oracle, governance). kucoin / okx =
custodial counterparty. Our instrument-death work measured CEX perp delistings and transfers to neither.

---

## #4 — Stable-pair LP fees · CLOSED, the gross never reaches the cost

Raw fee yield across 129 clean pools, 58,908 observations: p10 **0.000** · median **0.055** · p90 0.904 · p99
5.583 bps/day; TVL-weighted 0.230.

Minimum position for gas to fall below 10% of thirty days of fees at the median pool:

| gas round trip | $2 | $5 | $15 | $40 |
|---|---|---|---|---|
| minimum position | $122k | $304k | $913k | **$2.43M** |

**83 of 129 clean pools are on Ethereum, the most expensive chain.** The gross does not clear the cost, so
nothing was annualised.

**The adverse leg cannot be measured with this collector at all** — `lp_snapshots` holds APY, TVL and volume,
but no pool price, no reserves, no reference. **So the fee yield is one side of a ledger.** An AMM liquidity
provider is a passive maker **who cannot cancel**; our own maker work priced the analogous side on **661,177
real passive fills**: 20.87 bps captured at fill, **−1.29 bps kept 60 seconds later.**

**The yield lives exactly where the risk is unmeasured**: 75 of 132 clean pools are concentrated-liquidity,
carrying **3.2× the full-range yield** (0.097 vs 0.030 bps/day) — and in-range time is unmeasurable without
pool prices. Only **14 pools are USDC–USDT**; the rest pair long-tail issuers.

**#4's fee side is readable today. Its adverse leg has no readable-on date — it needs a different collector,
not more time.**

---

## The yardstick had a bug of its own

The rule *"express the return as a premium over the ~2.25% euro risk-free rate"* **produced a wrong
comparison, because the yields are denominated in USD.**

Hedged back to euro, covered interest parity makes the hedge cost ≈ (USD risk-free − EUR risk-free), so what
accrues is the spread over the **USD** risk-free rate. Unhedged, you hold an EUR/USD position that moves 1% in
days against a **1.1 pp annual** premium.

**If the USD risk-free sits at or above ~3.4%, every lending series is zero or negative**, and the whole yield
is payment for counterparty or contract risk. **This applies retroactively to #1 too** — funding is
USDT-denominated. It does not change carry's conclusion, but it widens the gap rather than narrowing it.

---

## The defect class — nine instances of one mistake

**A statistic computed correctly that describes the wrong thing.**

1. Markout measured from trade price instead of mid.
2. A "3.7% spread" that was an empty book.
3. Reported volume read as liquidity on a venue with a volume floor.
4. `pct_cycles_positive` as a level test, blind to oscillation.
5. The mean of a right-skewed funding series (`_modelled_rate`).
6. `NRestarts = 0` meaning the counter reset with the machine.
7. `max_basis_bps` testing a lookback mean instead of the condition itself.
8. Annualising 3–8 bps over four days into a percentage the spread eats several times over.
9. **A euro benchmark applied to USD-denominated yields — in a rule we wrote ourselves.**

And a related family: **a rate compared to a constant without its interval** — the T54 selector bug, fixed
there and left unfixed in the exit rule for two weeks, plus a fourth site in `selector.evaluate`'s trailing
gate.

## The verification rules this produced

- **A check that cannot fail is not a check.** The requirement that the new basis reconciliation *be able to
  fail* caught a real defect on its first run: a bare `0` literal made Postgres infer INTEGER and truncate
  **every sub-dollar basis figure to zero**, in the backfill and in live code. An identity check would have
  passed.
- **A backup that has never been restored is a hypothesis.** Three defects were found only by performing the
  restore.
- **A NULL derived column is not a missing observation.** `annualized_pct` was 100% NULL below dte 0.5 d while
  `basis_bps`, `future_price` and `spot_price` were 0% NULL, with observations to 2.3 minutes before expiry.
  Store inputs beside outputs and this is checkable.
- **A "NEXT" line in a dated document is not evidence work is still open** — and a date list carried between
  contexts is not a calendar. Both produced false claims in this project.
- **Declining a statistic is a valid result.** Realised ÷ marked was refused for #2 because the denominator
  was ~0; orthogonality was refused because the comparison was against a near-constant.

---

## What remains

| | |
|---|---|
| **#1 window with a live basis leg** | **read 2026-09-14 — see below** |
| **Pre-committed threshold** | the both-legs net must have a **confidence interval that excludes the USD risk-free rate**. If the interval covers it, close — regardless of how the point estimate looks |
| **Not about yield** | ship the backup off-host (~2 min, user action); decide what happens to live positions when the host dies |

---

## #1 perp funding — the verdict window, read 2026-09-14

**#1 is the one candidate of the four that did NOT close.** Measured over the window the threshold named —
generation 18 onward, **2026-09-04 06:06:13Z → 2026-09-14 06:33Z, 10.019 days**, both legs, mid-to-mid, open
book marked to market:

| | |
|---|---|
| funding income | **+$7.3729** |
| basis P&L (marked) | **−$0.3098** |
| entry cost (in window) | **−$0.5306** |
| exit-cost provision (realised median, 9.4 bps) | **−$0.6742** |
| **net, both legs** | **+$5.8583** on mean deployed capital $892.18 |
| **annualised** | **+23.92%** (+22.20% with added margin in the base) |
| **95% CI, bootstrap over days** (n=10, capital-day weighted, B=50k) | **[+15.83%, +32.10%]** |
| **95% CI, bootstrap over positions** (n=7) | **[+11.01%, +37.99%]** |
| **comparator** | **SOFR 3.60%**, USD, mid-September 2026 |
| **verdict** | **WITHDRAWN 2026-09-14 — see below. The interval excluded the rate, but the book was not the one the strategy chose** |

**WITHDRAWN THE NEXT DAY, and the threshold is unchanged.** The exit path had been broken since 2026-09-04:
`close_group` passed two untyped SQL parameters, so **every** attempted close raised, and R4's **2,595** exit
requests in the window were all denied. The measured book is therefore the chosen book **plus every position the
strategy tried to sell and could not** — 55+ hours of it, 23% of the window. The arithmetic was right and
described the wrong thing, for the second time (16.4% was the first). **The standard is not moved:** the
both-legs net must have a confidence interval excluding the USD risk-free rate. It must now be measured on a
window in which exits execute, which begins **2026-09-14 07:46:41Z**.

**The first honest exit datum, n=1.** `gate/INX_USDT` closed 07:47:34Z, 53 s after the fix was deployed — the
first completed round trip since 2026-09-02 and the first with both basis marks recorded live. Held 5.95 d on
$173.94: funding **+$1.0819**, entry **−$0.3754**, exit **−$0.1467**, basis **+$0.1491**, **net +$0.7089
(+16.7% annualised)**. **Actual exit cost 8.43 bps against a 17.57 bps provision — conservative by 2.1×**, the
first evidence the provision *overstates* rather than understates. **Cost ÷ income 48.2%**, against 246% over all
41 pre-fix round trips. One observation; it is not an average and must not be used as one.

Excluding the best single position (BTW) gives **+18.09%**; excluding the worst (GUA) **+27.88%**; excluding both
**+22.14%**. Unlike the previous window, where POWER alone decided the sign, **no single name decides this one.**

**Why this is a passage and not a yield.** Three facts travel with the number and none of them is optional:

1. **Zero completed round trips.** Nothing has closed since 2026-09-02 08:04Z. The entire result is accrual on an
   open book plus an unrealised mark. The exit is a provision calibrated to the realised median (9.4 bps) and
   stress-tested to the worst ever paid (55.9 bps → **+10.4%**, still clear) — but it has not been paid.
2. **The whole run is +2.72% annualised, below SOFR.** The 41 closed round trips lost **−$7.9899**, cost ÷ income
   **246%**. The window clears the bar because the pre-commitment correctly placed it after the churn regime; the
   record as a whole has not yet paid.
3. **76.2% of the income is the above-default tail, which is decaying inside the window.** At the venue default
   the same window annualises to **+0.99%**. Second ÷ first half mean rate: 0.25 · 0.40 · 0.46 · 0.52 · 0.89 ·
   1.01 · 1.48. The decay has not yet reached the net (second half **+20.71%**, last 3 days **+20.84%**).

**So the unifying finding of this ledger survives with one exception drawn in its own terms.** Funding still rests
at an administered default — 24.4% of the window's 340 receipts pay exactly `5e-05` — and at that default the
trade pays **+0.99%** against a 3.60% risk-free rate. What clears the bar is **not** the administered number; it
is the above-default tail, which is a real market price, and which this project has separately established
**rotates**. **#1 stays open because the tail was there for ten days, and it will close the moment the tail is
not.**

**What would settle it, and what would not.** A completed round trip in the fixed engine would settle it. More
accrual days on the same seven open positions would not — they add capital-days to a number whose uncertainty is
not in its funding noise. The open items are therefore **G2 (capacity)** and **G5 (what happens to live positions
when the host dies)**, not further yield work.

**Operational facts that would matter to any live deployment.** `apt-daily-upgrade` runs unattended every
morning ~06:10–06:35Z, but it only restarts every service on the box **when a library upgrade actually lands** —
measured over the 19 days to 2026-09-14 that was **three days, not nineteen** (08-27, 09-01, 09-10); it ran on
09-14 06:22Z and restarted nothing. The honest form is **unpredictable and roughly weekly, unwatched.** The box
has no UPS and runs on WiFi; two power losses and one network outage in ten days. And a new one, found in this window and
**root-caused the next day**: the carry bot's cycle raised on every tick for **55+ hours in total across five
episodes** (09-05, 09-10→11, 09-13→14), logging **2,554 identical `cycle failed` lines** while systemd reported
`active (running)` throughout. It was **not** a stalled thread — the loop is single-threaded, and the exception
landed **after** the risk events were written and **before** the accrual was, so the heartbeat stayed green while
the strategy did nothing. The cause was two untyped SQL parameters in `close_group`, which made **every exit fail
whenever the book curve was missing** — i.e. exactly when a name had to be sold into a thin book.
**No receipts were lost** (each resume caught up every epoch at its own rate), but for 23% of the verdict window
no position could be entered, exited or re-ranked. **The lesson is not "probe the right thread" — it is that any
liveness signal emitted before the point of failure will keep reporting health while the work behind it fails.
Measure the output, not the attempt.**
