# BLUEPRINT — the plan

**This file is the plan. Everything else is appendix.** Edited in place, never appended to; history lives in git.
If something here is stale, fix it here — do not write a new status document.

Last updated: 2026-09-14 · Supersedes `CURRENT_STATUS.md`

---

## 0. Why this file exists

Three different kinds of knowledge were being stored the same way — as dated prose — which is what makes work
feel like random motion: **the open-questions list gets buried inside narratives, so whenever something breaks,
the break becomes the plan.**

| kind | belongs in |
|---|---|
| **Settled facts** — won't change | **§3**, written once, never re-derived |
| **Open decisions** — the actual plan | **§2**, as gates |
| **Events** — disposable after a week | Brain summaries / git |

## 1. The goal

Find a **direction-neutral yield** that survives honest measurement — real fills, real fees, real tail risk — and
determine **at what size it is worth operating**, judged as a premium over the euro risk-free rate (~2.25%) after
tax and off-ramp, never as a headline APR.

Not: predicting price. Not: a bigger backtest.

---

## 2. Decision gates

A question without a pre-committed consequence is a hobby. Each gate states what would answer it **and what we do
for each answer**, decided before the data arrives.

### G1 — Does the carry (perp funding) yield hold across regimes?
- **Status: SUSPENDED (2026-09-14), awaiting a window in which exits can execute.** The
  **+23.92% [+15.83%, +32.10%] is WITHDRAWN as a strategy result.** Not because it was computed wrongly — the
  arithmetic, the leave-one-out, the exit-cost stress to 55.9 bps and the 79 bps needed to reach SOFR all stand —
  but because **the book it measured is not the book the strategy chose.** It is that book *plus every position
  the strategy tried to sell and could not*, across 55+ hours and 23% of the window. **The threshold itself is
  unchanged and is NOT moved:** the both-legs net must have a confidence interval that excludes the USD risk-free
  rate. What is withdrawn is the measurement, not the standard. This is the second time a correctly computed carry
  number has described something other than what it was believed to describe (16.4% was the first), and both times
  the cause was the same: **the instrument was broken in a way the number could not show.**
  **Reopened by:** a window that begins after the 2026-09-14 07:46:41Z restart, in which exits actually execute.
  The first datum exists (§5, `gate/INX_USDT`) and it is n=1.
  **The withdrawn measurement, kept for the record only — do not quote it as a result:**
  Measured 2026-09-14 over the window the threshold named — generation 18 onward, **2026-09-04 06:06:13Z →
  2026-09-14 06:33Z, 10.019 days** — both legs, mid-to-mid, open book marked to market:
  funding **+$7.3729**, basis **−$0.3098**, entry cost **−$0.5306**, exit-cost provision **−$0.6742**, net
  **+$5.8583** on mean deployed capital of **$892.18**. Annualised **+23.92%** (**+22.20%** with the $69.10 of
  added margin in the base). **95% CI [+15.83%, +32.10%]** — block bootstrap over **days** (n=10, ratio estimator
  weighting by capital-days, B=50,000); over **positions** (n=7) it is **[+11.01%, +37.99%]**. The comparator is
  **SOFR 3.60%** (USD, mid-September 2026), not the euro rate — the yields are USDT-denominated and covered
  interest parity makes the hedge cost ≈ (USD rf − EUR rf). **Both intervals exclude 3.60%; the premium is
  +20.3 pp at the point estimate and +12.2 pp at the interval's lower bound.**
  **No single name decides it.** Leave-one-out spans **+18.09%** (drop BTW, the best) to **+27.88%** (drop GUA,
  the worst); ex-both **+22.14%**. Every one of the seven leave-one-out values clears 3.60% by more than 14 pp.
  Nor does the exit assumption: the provision is the **realised median exit cost of the 41 closed round trips
  (9.4 bps)**; at their realised **p90 (19.5 bps)** the window is **+21.0%**, at the **worst ever observed
  (55.9 bps)** it is **+10.4%**, and reaching 3.60% would need **79 bps — 1.4× the worst exit this book has ever
  paid.** Death risk is not in the interval and does not close it either: at the book's venue mix (71.8% MEXC)
  blended death is ~20.6%/yr, costing **1.6 pp/yr at mean LGD and 13.7 pp/yr at the p95 LGD of 66.5%** →
  **+22.4%** / **+10.2%**.
  **Three things make this a passage, not a yield, and they must be quoted with it.**
  (1) **Zero round trips completed inside the window — and root-caused on 2026-09-14 as a BUG, not a quiet
  market.** Nothing has closed since 2026-09-02 08:04Z; the window contains 2 opens and 0 closes. The reason is
  that `close_group` raised on every attempt (§3): R4 requested **2,595 exits** in the window and **not one could
  be executed.** So the book under test is not the book the strategy chose — it is that book **plus every position
  the strategy tried to sell and could not**. Every dollar is accrual plus an unrealised mark, and this project has
  already been taught once that a mark is not a fill (arb: mark 95.7% win → executable 0% win, −127 bps).
  **The fix is committed but not yet deployed; on restart `gate/INX_USDT` closes immediately (§5).**
  (2) **The whole run, marked to liquidation, is +$2.0795 over 26.09 days = +2.72% annualised — BELOW SOFR.**
  The 41 closed round trips lost **−$7.9899** (funding +$5.94 against **$14.62** of entry+exit cost;
  **cost ÷ income 246%**). The window clears the bar only because the pre-commitment placed it after the churn
  regime — which was the right call, but it means **the record as a whole has not yet paid.**
  (3) **76.2% of the window's income is the above-default tail, and the tail is decaying inside the window.**
  Of 340 receipts, 24.4% pay exactly `5e-05`, 74.7% above it; **at the default rate the same window annualises to
  +0.99%.** Second-half ÷ first-half mean rate: INX 0.25 · HANA 0.40 · POWER 0.46 · LYN 0.52 · BTW 0.89 · H 1.01 ·
  GUA 1.48. The decay has **not yet** reached the net — second half alone **+20.71% [+8.93%, +32.58%]**, last
  3 days **+20.84% [+8.30%, +33.34%]**, both still excluding 3.60%.
  **So G1 is answered on its own terms and hands the programme to G2 and G5, with the standing condition that a
  completed round trip — not another accrual day — is what would confirm it.**
- **Answered by:** accumulating settlements across generations for several more weeks. Gaps excluded; no unbroken
  window needed. **No repair shortens this.**
- **≥ ~10% net APR persists →** proceed to G2. **Collapses to the resting state →** carry is a deposit with
  exchange risk; park it.

### G2 — How much capital can a book absorb?
- **Status:** open, and unanswerable by observation. Per-name depth measured between **$584 (top-5)** and
  **$19,314 (50-level)** — a 33× spread.
- **Answered by:** a minimal live execution test measuring realised slippage, sized so the maximum loss is worth
  less than the information.
- **Capacity × premium < cost of operating →** park regardless of G1. **This is the gate to any real capital.**

### G3 — Is any of #2 / #3 / #4 both orthogonal and payable?
- **Status:** all three collecting since 2026-08-24. **#2's first settlement has now been observed end to end,
  and it does not survive contact with cost.** See §3. The 2.7–4.5% annualised mark was real as a *price* and
  worthless as a *trade*: annualising 3–8 bps over a 4-day horizon makes a percentage that the round trip eats
  several times over. **Priority reverts to "none of the three has cleared the yardstick".**
  - **#2 dated basis is the only one that does not rest at a default** — but its median quoted basis is
    **7.6 bps** against a median held-to-settlement cost of **17.6 bps**, and **0 of 25 instruments clear cost at
    a 3 bps/crossing fee**.
  - **#3 lending: all six earnable series sit in a 3.35–3.65% band, and the comparison to a 2.25% EURO rate is a
    currency mismatch.** Four of the six are constants. The one live rate (Aave) pays its tail only when the exit
    is shut. See §3.
  - **#4 LP: median raw fee yield is 0.055 bps/day**, against a gas break-even that needs **$122k–$2.4M** of
    position depending on chain — and the adverse leg is **not measurable** from this collector at all. Per the
    rule #2 taught us, the gross does not clear the cost, so nothing here gets annualised. See §3.
- **Answered by:** the §4 yardstick, per the readable-on dates in §5.
- **None clears the yardstick →** the answer to the project is "no yield here", reached on evidence.
- **Priority follows the evidence: #2 first.**

### G4 — Can a book be held at a concentration that survives a bad event?
- **Status:** partially answered and currently failing. Largest carry position ~24% against a **6.7%** line;
  **15 names needed** for a p95 death not to be a portfolio event, **77** for a line item; **15–16 of 153 pass.**
- **New constraint from #4:** diversifying across LP pools **increases** the number of distinct contract systems
  you are exposed to (13 chains, ~20 projects). **Diversification and contract-risk reduction pull against each
  other** — the CEX framework never had to model this.
- **Answered by:** a per-name weight cap (measure yield given up at 6.7% / 10% / 12.5%), then re-count the
  passing set **after** the mean→median fix, which is predicted to shrink it.
- **Cap measured 2026-09-04, step 1 of 3 done, under the CURRENT (mean) estimator.** The cap never binds by
  forcing money down the ranking — it binds by leaving capital idle, because the passing set is too small.
  Mean deployable capital and net APR (idle held at the 2.25% risk-free anchor, APR-on-deployed 19.45%):
  **6.7% cap → 14.9 names needed, 50.5% deployable, 10.9% net (−5.9pp) · 10% → 10.0 names, 75.0%, 15.2% (−1.6pp) ·
  12.5% → 8.0 names, 87.0%, 17.2% (not binding) · uncapped today 84.3% deployed, 16.8%.** At the 6.7% line the
  strategy is a **10.9% paper number against a 2.25% anchor**, before tax, off-ramp, and any basis P&L.
  The book has never once been able to fill a 6.7% cap: **0% of 212 selection cycles had ≥15 passing names.**
  Live weights today are 21.4 / 16.0 / 13.7 / 4.8 / 3.8% — largest name 3.2× the 6.7% line.
- **What the R7 venue cap is worth, measured 2026-09-04.** MEXC yielded **34.9% gross APR** against Gate's
  **24.0%** and carried 80% of income on 73% of the capital-days. Enforcing 40% correctly, with Gate capacity as
  observed, caps MEXC at (0.4/0.6)× Gate deployment: total deployment falls to **37.5%** of the fund and gross
  income from **$7.02 to ~$2.80 (−61%)** — roughly **16.8% → ~8.5% net APR**. Against that, the cap moves blended
  death from **23.5% to 16.5%/yr**, worth **0.5 pp/yr** at mean LGD (7.56%), 0.25 pp at median, and **~4.6 pp** at
  the p95 LGD of 66.5%. **So R7 as specified costs ~8 pp to buy ~0.5 pp of expected value: it is tail insurance,
  not an expected-value trade,** and you must believe the p95 tail to want it at this price.
  **Sequencing consequence — fix R4's interval-awareness BEFORE R7, not after.** Gate capacity is low here only
  because R4 is force-exiting Gate's 8 h names whenever funding returns to default (§3). Enforce R7 first and you
  pay the full 8 pp for a shortage that the R4 fix would have removed. Both fixes belong in their own sessions;
  the order is not interchangeable.
- **Passing set below ~15 →** cannot be diversified enough to run, whatever the yield.

### G5 — What happens to live positions when the host dies?
- **Status:** open. Two unclean stops in three days; no UPS; a live bot would be blind 16–32 min.
- **Answered by:** a choice, not data — UPS for clean shutdown, an external watchdog that alerts or flattens on
  silence, a documented restart-into-known-state procedure, or a different live host.
- **Nothing goes live until this is written down.**

---

## 3. Settled — do not re-derive

Edit a line if a new result contradicts it; never add a second.

**Yield structure**
- **Perp funding rests at a venue default** — median exactly `5e-05` on gate, mexc, bitget, bybit, kucoin, okx;
  ~5–15% of names pay above it, and that tail rotates. **Adding venues does not enlarge the opportunity set.**
- **Pinning is sticky** — after 2 settlements at/below default, 84% are still there 21 settlements later.
- **CEX lending rests at defaults too** — kucoin USDC/USDT supply 3.650% (raw exactly 0.0001/day), okx 3.000%
  (raw exactly 0.03), one distinct value in 611 observations. Aave moves continuously; CEX does not.
- **Dated basis does NOT rest at a default** — p50 by venue: gate 2.72 / okx 3.72 / binance 3.73 / bybit 4.40 /
  deribit 4.53. Five independent venues agreeing is the evidence.
- **Stable-LP fee yield is right-skewed 9×** — clean set (116 pools) median **0.192%**, mean 1.722%.

**Costs**
- **Dated basis cost is the futures leg** — 92–97% of the roundtrip. Median roundtrip: binance 6.60 / gate 18.41 /
  bybit 20.58 / okx 23.31 bps. **At a 3.7% median, a 30-day contract grosses ~30 bps against that cost; a 7-day
  contract grosses ~7 bps and cannot cover it.** Dated basis is a longer-hold trade or it is nothing.
- **Aave's high rate is the price of being locked in** — USDC sat at 12.54% for 23.1% of observations, and at
  those moments utilisation was **0.99975** (supply $2.031bn ≈ borrows $2.031bn). When utilisation fell to 92.4%
  the rate went to 3.71%. **The high rate and the inability to exit are the same variable.**
- **Carry engine cost ÷ income is 246% over ALL 41 completed round trips — 87.9% was a favourable subset.**
  The 27% was a partial-lifecycle number (21.5 h, exit cost unbooked); 87.9% was the 14 round trips that opened
  *and* closed after the Phase-3b fixes (income $5.69, cost $5.00, net +$0.69). Over the full closed set:
  **funding +$5.9396 against $14.6182 of entry+exit cost, net −$7.9899.** Realised cost per round trip, in bps of
  notional: **entry p50 10.1, exit p50 9.4, exit p90 19.5, exit max 55.9.** The engine fixes are real
  (rebalances 491/day → 3; 0 errors) — **the cost ratio never was, and the churn is what made it.**
- **Carry has a break-even holding period of ~3.5 days**, and it is sharp. Round trips post-fix, by hold length:
  **<2 d → cost 1665% of income (0/2 profitable) · 2–3.5 d → 133% (0/3) · 3.5–6 d → 47% (6/6) · >6 d → 43% (3/3)**.
  Nothing held under 3.5 days has ever made money. **The R4 funding-flip exit and the entry payback gate are in
  conflict:** entry requires the round trip to repay within `max_payback_days`, and R4 then exits at 2.6–3.3 days,
  before it does. Every loss in the window came from that pair, not from the funding rate.

**Risk**
- **Instrument death** ~12.3%/yr batch-adjusted (MEXC 24.3% vs Gate 11.3%); LGD mean 7.56% / median 3.64% /
  **p95 66.5%**, the tail resting on one observation (VANRY).
- **Death warning fails where it matters** — already-wide names give no advance signal (0.91× vs control).
- **The modelled funding rate is optimistic by ~15%, not by 2×.** Re-measured on 394 settlement receipts across
  13 names over 8.9 days: **realised ÷ modelled median 0.85**, dollar-weighted 0.835, quartiles 0.55 / 0.85 / 1.06.
  The earlier 0.50 (n=13 positions, CI 0.34–0.83) was the same statistic on a quarter of the data and is
  superseded. **The bias does not drift with holding period** — pooled by day-since-open the ratio is flat and
  noisy (0.65, 0.75, 0.64, 1.11, 0.93, 0.63, 0.83, 0.98, 1.63), so the defect is a fixed estimator bias and the
  mean→median fix in `_modelled_rate` is still the right correction, worth ~15% rather than ~50%.
  Per-name ratio is confounded by the exit rule, not by decay: the low ratios are all short holds
  (IDOL 0.20 at 3 epochs, BTR 0.47 at 9) because R4 exits a name *when* its funding collapses. This is consistent
  with, not a reversal of, the earlier rejection of post-selection decay.
- **The R7 MEXC venue cap is not enforced on the standing book — only on the marginal allocation.** In
  `selector.allocate()` the `mexc` counter resets to 0 each pass and `budget_mexc` is charged only against *new*
  room, so positions already open never consume it. Measured drift over 9 days: MEXC share of deployed capital
  **51.0% → 64.8 → 74.4 → 89.9 → 93.6%**, against a 40% cap. It matters because MEXC carries 24.3%/yr instrument
  death vs Gate's 11.3%: the book concentrates into the venue with double the death rate, silently.
  **Re-measured 2026-09-14, still unfixed, and it moved BOTH WAYS inside one day on a single name:**
  93.5% for 09-04→09-08, then **71.8%** once one Gate name (INX, $174) opened on 09-08, then **back to 80.8%**
  at 08:00Z when INX closed and the selector replaced it with mexc/BULLA ($74) and gate/FF ($111). Still
  **2.0× the 40% cap**. Largest single name **36.3% → 24.4% → 21.4%** against the 6.7% line (**3.2×**). None of
  this is R7 working — the venue mix is a by-product of which single name happens to open or close, which is
  exactly the fragility a cap exists to remove.
- **Only one of the carry position's two P&L legs is booked, and the missing one is the same size as the
  answer.** Long spot + short perp earns funding *and* the change in the spot–perp basis between entry and exit;
  `close_price` is NULL on every leg, so the second is never booked. Reconstructed mid-to-mid over the window:
  **−$3.53 ± $1.38 against a booked net of +$4.30** — 82% of the reported P&L. On *completed* round trips the term
  is **−$0.04 ± $0.52** (indistinguishable from zero); the whole of it sits in the open book, and **$3.24 of the
  $3.49 is one $27 position** — `gate/POWER_USDT`, whose basis series went to −900…−1250 bps and stayed there.
  **So the term is immaterial on 13 of 14 positions and catastrophic on one, and the one is a data failure rather
  than a market move.** No carry APR is quotable until the basis leg is marked at exit.
- **FIXED 2026-09-04 — the basis leg is booked, mid-to-mid, with costs as a separate line.** Convention chosen:
  the basis term runs from the mid basis at entry to the mid basis at exit, and `entry_cost_usd`/`exit_cost_usd`
  stay as the explicit charge for crossing the spread. Fill-to-fill was rejected because it hides execution
  quality inside the basis term *and* because the fills already contain the round trip that the cost columns
  charge again. Each mark is a **trailing 2 h median of the mid basis**, the same estimator at entry and exit and
  the same one used for the backfill — one point read is not a measurement when intraday basis SD is 9–59 bps.
  `close_price` is now populated on exit (as an INPUT beside the marks, never as what the P&L is struck from);
  historical rows keep it NULL because the fills cannot be reconstructed. `paper_pnl_usd` is redefined as
  `funding_only_pnl_usd + basis_pnl_usd`, with the old funding-only series preserved in its own column so the two
  are comparable **across** the break. **Backfill: 46 positions, 41 of 41 closed positions fully marked.**
  Verification is a genuine two-way reconciliation — the total derived from receipts plus marks against the total
  in the redefined column — agreeing at **−$1.525133 vs −$1.525133 (1.8e-15)**, and `tests/test_basis_booking.py`
  demonstrates the double-count check *can* fail by running the wrong implementation beside the right one: the gap
  is exactly one round trip.
- **The reconciliation earned its keep on its first run: it caught a silent truncation to zero.** The backfill's
  `CASE WHEN leg='spot' THEN $9 ELSE 0 END` let Postgres infer the parameter's type from the `0` literal, make it
  INTEGER, and truncate every sub-dollar basis figure to zero — and the *same* pattern was in the live
  `close_group`. The first apply reported `basis $+0.0000` against a computed `+$0.7459`. **An identity check
  would have passed.** Both sites now cast `::double precision`.
- **THE DEFECT CLASS, not three unrelated bugs: a summary statistic standing in for the condition being tested.**
  Four instances now. `_modelled_rate`'s mean of a skewed series (realised = 0.50 of model); `max_basis_bps`
  against a 14-day mean; the reported-volume band that selected manufactured tape by construction; and R4's flat
  annual floor below. Every one takes a distribution, replaces it with its centre, and then tests the centre for a
  property only the tail has. **The rule: a guard must be evaluated against the quantity it names, at the moment
  it acts.**
- **FIXED 2026-09-04 — `max_basis_bps` now binds on the CURRENT basis.** The binding test is the median mid basis
  over a trailing 2 h window; the 14-day mean is kept as a strictly looser secondary test (2× the gate) because it
  catches the opposite failure — chronically wild basis, one calm hour — and can only ever reject in addition,
  never admit. **Measured on the position that caused this:** at `gate/POWER_USDT`'s open on 2026-09-02 11:52Z the
  14-day mean was **−132.6 bps** and passed the 150 bps gate, while the contemporaneous 2 h basis was
  **−984.7 bps**. **POWER would now be rejected**, 6.6× outside the gate.
- **FIXED 2026-09-04 — R4's exit floor is interval-aware, and so is the selector's entry gate (the FOURTH site).**
  The floor is now one **per-epoch rate** (`5.479e-05`) anchored at a 4 h reference and carried to each name's own
  interval, instead of one flat annual constant applied to an interval-annualised rate. Under the old form the
  universal `5e-05` venue default — a rest value carrying no information about the name — sat at **91% of the
  floor for a 4 h name but 46% for an 8 h name**; it now sits at **91.3% at every interval**, so the exit means the
  same thing regardless of settlement schedule. **Floors: 4 h → 8.0% on capital (12.0% gross), unchanged, the
  anchor; 8 h → 4.0% on capital (6.0% gross), halved.** **Admitted set on the live universe: 4 h 195 of 800
  (unchanged), 8 h 31 → 281 of 397 (+250), 1 h 1 of 7 (unchanged); 227 → 477 names overall.** Split by venue on the
  8 h names, which is the point: **Gate 22 → 157, MEXC 9 → 124** — Gate gains more because its 8 h book is larger
  (217 vs 180), which is precisely the demand-side half of the venue drift. The **fourth site** was
  `selector.evaluate`'s G1 trailing gate, which tested the same bare constants; it now reads the same
  interval-aware floor, because entry and exit testing different metrics *is* the TUT churn bug. Grep found no
  fifth: `min_net_apr` is a selection threshold on annual yield, where an annual unit is correct.
- **RETRACTED — the adverse-exit hypothesis, and it was wrong in sign.** The reasoning was that a funding collapse
  compresses the basis and that this hurts the position. For long spot / short perp, **basis compression is a
  gain** — the short perp is bought back cheaper relative to spot. The measurement reversed it (Spearman −0.512;
  R4 exits averaged **+5.9 bps**). **R4's defect is that it is EARLY, not that it is ADVERSE, and the two do not
  compound.** Recorded so a future session does not re-derive it. The sign is now pinned by a test.
- **Every risk control priced against this window costs more than the edge it protects — and that sentence is
  misleading on its own, so the two halves must travel together.** Per-name weight cap at 6.7%: **−5.9 pp**, buys a
  p95 death not being a portfolio event. R7 venue cap at 40%: **~−8 pp**, buys 0.5 pp expected and ~4.6 pp at p95
  LGD. R4 funding-collapse exit: **$6.16 of $6.85** in-window. **The counter-argument:** this window holds zero
  deaths and 2.5% negative-carry epochs, so it contains **none of the events these controls exist for**. Insurance
  always looks overpriced in a year with no fire. **Neither half is safe to quote alone** — which is exactly why
  G1 is the binding gate and cannot be closed inside one regime.
- **A basis P&L reconstructed from the bot's own recorded entry prices is invalid — it double-counts the entry
  cost.** `executor.py:47-54` fills spot at the **ask** VWAP and perp at the **bid** VWAP, so the recorded entry
  basis is depressed by the full round-trip spread, which `entry_cost_usd` then books *again*. Measured:
  the recorded entry basis is below the contemporaneous mid basis in **14 of 14** positions, median **−23.5 bps**.
  Naive reconstruction gives −$6.05; mid-to-mid gives +$0.27 ± $0.65. **Use mid-to-mid marks, never entry_price.**
- **Point-in-time basis marks cannot measure a basis P&L on these names.** Intraday basis SD is 9–59 bps per name
  against moves of interest of 20–40 bps; every one of the 14 measured moves falls within ±1.9 SD of its own noise.
  A single observation at entry and exit is not a measurement — a ±2 h median is the minimum usable mark.
- **The exit rule is NOT adverse on the basis leg — the hypothesis was backwards.** Funding and basis are the same
  variable (leverage demand), so a funding collapse *compresses* the basis, which is a **gain** for long-spot /
  short-perp. Spearman(funding drop, basis move) = **−0.512** (Pearson −0.549, n=14, p≈0.06): larger funding drops
  came with more favourable basis moves. R4 exits (n=13) averaged **+5.9 bps**; the single R5-depth exit was
  −37.8 bps. **Suggestive and correctly signed, but n=14 against a ±1.9 SD noise floor — a hypothesis, not a
  finding.** R4's defect is that it is early (below), not that it is adverse.
- **Reverting to the venue funding default *automatically* trips the R4 exit floor on 8 h-interval names.** The
  universal `5e-05` default annualises to **10.95% at a 4 h interval but only 5.48% at 8 h**, against an R4 floor of
  **8%**. So an 8 h name that merely returns to the default — the base case, since the median *is* the default and
  pinning is 84% sticky — is exited by construction, with no information in the exit. **5 of 14 round trips were
  forced this way, all of them Gate.** This is the demand-side half of the venue drift: R4 evicts Gate names on
  schedule while MEXC's 4 h names sit above the floor, so the book ratchets toward MEXC without R7 ever being
  consulted. **R4's floor must be interval-aware.**
- **Holding longer beat the exit rule at every horizon tested, with no interior optimum.** Same receipts, R4's
  funding-collapse exit suppressed below a minimum hold, P&L on the 14 completed round trips:
  **actual 0.69 · 3.5 d 1.39 · 4 d 1.60 · 5 d 2.49 · 6 d 3.80 · 7 d 3.44 · 8 d 3.78 · 10 d 5.13 · 12 d 6.44 ·
  14 d 6.85.** Monotone to the configured 14-day target, and **negative-carry epochs stay at 2.5%** (10 of 404).
  Exposure lengthens 1.09× at 3.5 d, 1.38× at 6 d, 2.14× at 10 d. **This is not an overfit — it is a null result on
  the parameter:** the window contains almost no negative funding and zero deaths, so it holds no instance of the
  risk R4 exists to manage. It says R4 as configured destroyed value here; it cannot say what the floor should be.
- **`paper_pnl_usd` is an identity, so "it reconciles to the cent" proves nothing.** Residual of
  `pnl − (realised − entry − exit − remediation)` is **exactly 0.00000000 across all 92 legs** — because that is
  how it is computed. `close_price` is **NULL on every closed leg**: no spot/perp basis change is marked at exit,
  no price P&L of any kind is booked. The paper carry P&L is funding minus modelled costs and nothing else.
- **WITHDRAWN 2026-09-14 — "the R4 interval fix worked" was read off a broken exit path.** The 2026-09-14 claim
  was that holds lengthened (41 round trips at a **median 0.00 days** before; 7 of 7 open past **3.5 days** after)
  because R4 had stopped exiting by construction. **That is not why.** R4 fired `FIRED->exit` **2,595 times**
  between 2026-09-05 and 2026-09-14 and **every single one was denied by an exception** — the exit path has been
  incapable of closing a position since the 2026-09-04 migration. Holds did not lengthen because the rule improved;
  they lengthened because **the bot physically cannot close.** The interval-aware floor may still be correct — the
  8 h arithmetic in `tests/test_basis_booking.py` is unchanged and still passes — but **this window contains no
  evidence either way, because no exit it requested was ever executed.** The honest state of R4 is *untested in
  production since 2026-09-04*.
- **CORRECTED 2026-09-14 — the exit did NOT fail "precisely when the book was thin". It failed on EVERY exit,
  unconditionally, and no thin book was ever involved.** The earlier claim was that `close_carry` returning
  `(None, None)` was the trigger. It was not. **Postgres infers parameter types at PREPARE, from the SQL text
  alone — the values are irrelevant.** Demonstrated directly against the live database: the untyped statement
  raises `DatatypeMismatchError` for `(None, None)`, for `(0.006, 0.0059)` and for one of each. So all **2,554**
  failures were the type error and **none** was an unpriceable book. The `(None, None)` return was what drew the
  eye to the line; it was never the condition. **This matters because the two readings imply opposite things: a
  bug that fires only in a crisis is a risk-control failure, while one that fires always is a total outage that is
  easier to find. This was the second.** The measurement behind the correction is in §5.
- **WITHDRAWN 2026-09-14 — the chain "depth silence -> no book curve -> the position cannot be sold" is wrong at
  its middle link.** `latest_curve` takes `max(ts)` with **no age bound**, so a dead feed never yields *no* curve;
  it yields a **stale** one. And depth does not vanish once present: **0 of 101,576 snapshots** across 608 legs
  had fewer than 3 usable levels, the thinnest ever seen had **21**. The only unpriceable legs belong to
  **2 of 153** names (`gate/HOODX`, `gate/AI`, both missing perp), and **neither can ever be held** because
  `open_carry` requires the same market's curves. **A name that can be bought can be sold.**
- **KEPT AND PROMOTED — the admission rule was already doing the work, silently and by accident, and that is a
  DEPENDENCY nobody is maintaining.** The reason the unpriceable exit has never occurred is not a safeguard anyone
  designed: it is that `executor.open_carry` refuses to open a leg it cannot price, which happens to exclude
  exactly the names whose exit would be unpriceable. **An accidental safety property has no owner and no test.**
  If `open_carry` ever stops requiring both curves — a relaxation that would look like a small liquidity
  concession — **the exit exposure appears with no other warning anywhere in the system.** Treat any change to
  `open_carry`'s curve requirement as a change to the exit path.
- **THE RULE: fixing an instance of a defect class is not fixing the class.** The comment explaining the `$10`
  INTEGER truncation sat **two lines below** a parameter that still had the same defect, and it stayed there for
  ten days. Runtime-dependent parameter typing has now appeared **three times**, twice in the same statement. The
  remedy is a sweep with a test behind it, not a patch: `tests/test_sql_param_types.py` asks **Postgres** what it
  infers for every parameter in the carry tree and fails on either mode — **HARD** (the statement cannot PREPARE,
  which is value-independent: if it prepares, no input can make it raise) and **SILENT** (a parameter inferred
  *narrower* than its column, which truncates without an error, as `$10` did). **Swept 2026-09-14: 33 statements,
  99 parameter→column pairs, 0 defects remaining** — and the test reintroduces the original untyped `CASE` to
  prove it can still fail. The same method found a fourth site when interval units were swept; guessing would not
  have.
- **THE BUG: `close_group` passed two untyped parameters, and it broke the exit leg for nine days.**
  `close_price = CASE WHEN leg='spot' THEN $5 ELSE $6 END` — with both arms untyped and both values NULL,
  Postgres has no anchor, infers **TEXT**, and the UPDATE raises `DatatypeMismatchError` against a
  `double precision` column. The fills are NULL exactly when `executor.close_carry` cannot find a book curve, i.e.
  **when the name must be exited into a thin book — the one moment an exit matters.** Fixed by casting both to
  `::double precision`. **This is the same defect, in the same statement, as the `$10` INTEGER truncation caught on
  2026-09-04 — the comment explaining that bug sits two lines below the one that still had it.** Fixing an
  instance of a defect class is not fixing the class; the adjacent parameters must be audited at the same time.
  `tests/test_liveness.py` reproduces the failure against the live database and passes only on the cast form.
- **THE UNPRICEABLE EXIT IS NOW A NAMED STATE, NOT AN EXCEPTION.** Casting the parameters makes the UPDATE
  succeed; it does not answer what the bot should do when it cannot price an exit, and "raise, log and retry
  forever" is what produced 2,554 identical failures and a silent book. The behaviour is now explicit and counted:
  when the fills are NULL the bot emits an `exit_unpriced` event, increments `unpriced_exits`, and — in **paper**
  — closes at the pessimistic modelled sweep cost with `close_price` left NULL and the close **tagged
  `[UNPRICED]`** in `notes`, so these exits can be segregated from the exit-cost statistics instead of being
  silently averaged into them. **`CARRY_ALLOW_UNPRICED_EXIT=0` makes it refuse instead**: the position stays open,
  loudly, and a human decides. **The live policy is a decision the user still owes** — closing blind at a modelled
  price is a guess at a fill nobody observed, and that is a G5 choice, not a default.
- **The `basis-now` gate is firing in production and screens exactly the failure that created it.**
  ~**180 rejections** across 161 selection passes in the window (1–1.5 per pass), against `trail7` 10,059 and
  `payback` 10,690. Confirmed on the causing position: the 2 h median mid basis for `gate/POWER_USDT` ending at
  its own open (2026-09-02 11:52:35Z, n=24 observations) was **−961.9 bps — 6.4× the 150 bps gate.** POWER's
  basis was genuinely dislocated, not an artifact: Gate's median basis that day was **−530 bps** (min −1841), and
  it has never returned to the band (−40 to −110 bps daily median since, min −560 on 09-14). **POWER is a
  perp≠spot name, and the live gate now rejects its whole class at entry.**
- **A mark is not a fill, and the live basis marks are too few to test.** Only **2 of 48** positions carry a
  live-recorded entry mark (`live-median2h`: GUA −39.8 bps, INX +12.6 bps over 5.9 d); the other 46 are
  `backfill-median2h`. The backfilled closed set (n=41) is **median 0.0 bps, IQR 0.0 to +5.2, range −74.1 to
  +42.0**, total +$0.7459. The two live figures sit inside that range and **agree in scale (tens of bps)** — but
  n=2 cannot test agreement, and the only thing established is that the live path has produced **no POWER-class
  artifact**. In-window basis is indistinguishable from zero: **−1.27% annualised, CI [−7.52%, +4.25%]**.
- **The passing set has never reached the 15 names the concentration work requires.** Across 161 selection passes
  in the window the count ran **5.0–9.8/day (max 11, of 153 evaluated)**; combined with the earlier 212 cycles,
  **0 of 373 selection cycles have ever produced ≥15 passing names.** G4 is failing on supply, not on the cap.
- **A carry position's capital is not its notional — margin top-ups are real capital and are not in the
  denominator.** The open book has consumed **$69.10** of added margin (`mexc/BTW_USDT` **$57.15** on a $109
  notional over 36 remediations; `gate/POWER_USDT` **$11.95** on $24 over 40). Including it moves the window from
  **+23.92% to +22.20%** — small here, but it scales with rebalancing, and BTW is simultaneously the book's
  largest income source and its largest margin consumer.
- **Durability:** `data_checksums=on`, `fsync=on`, `full_page_writes=on`; **0 checksum failures for the cluster's
  whole life.** Corruption is not the risk; the single copy is.
- **#2 CONFIRMED on the repeat. The 2026-09-04 08:00Z settlement (22 executable instruments) reproduces the
  08-28 result in sign, magnitude and composition.** Median net is negative at **every** horizon (−5.3 to
  −11.1 bps vs −7.4 to −14.4 on 08-28); 6 of 22 clear cost at 4 d (7 of 25 before); and **every survivor is again
  BTC or ETH** — bybit/BTC +5.8, bybit/ETH +4.2, okx/BTC +3.9, okx/ETH +3.2, gate/ETH +1.3, gate/BTC +1.3 bps.
  No alt survived on either date. Costs were lower this cycle (median held-to-settlement 7.5 bps at 4 d vs 17.6),
  and the sign still did not turn. **Two events is still one regime, but the effect was never marginal and it
  replicated exactly. #2 is answered negative.**
- **#3: only 6 of 14 lending series are earnable at all, and 4 of those 6 are constants.** Borrow rates are what
  you pay, not what you earn. The supply side is: `aave-v3` USDC/USDT, `kucoin` USDC/USDT, `okx` USDC/USDT. Of
  those, **kucoin has ONE distinct value in 3,201 observations per asset** (3.65% flat, 100% at the mode), okx has
  **2 and 14** distinct values (3.50%, 70.6%/70.9% at the mode). Only Aave moves. **All six cluster in a
  3.35–3.65% band**, and all are `term=flexible` — no lock-up, no notice period.
- **#3's headline premium is a CURRENCY MISMATCH, and it is the same error #2 taught us in a new costume.**
  Naively, 3.35–3.65% against the 2.25% euro anchor reads as **+1.10 to +1.40 pp**. But these are **USD**
  rates and the yardstick is a **euro** rate. Hedged back to euro, covered interest parity makes the hedge cost
  approximately (USD risk-free − EUR risk-free), so the premium that actually accrues is **the stablecoin rate's
  spread over the USD risk-free rate**, not over the euro one. Unhedged, you are taking an EUR/USD position that
  moves 1% in days against a 1.1 pp annual premium. **The USD risk-free rate is the one external number this
  determination needs and it is not in the database.** If it sits at or above ~3.4%, every series here has a zero
  or negative premium and the entire yield is compensation for Aave contract risk or CEX custodial risk.
- **#3: Aave's apparent 5.19% is the utilisation kink, and the high rate and the closed exit are THE SAME
  EVENT.** Time-weighted mean 5.187% against a median of 3.351% — the gap is **20.4% of observations sitting in a
  recurring 01:00–05:00Z nightly window** at 99.6% utilisation paying ~12%. In that window **free liquidity
  collapses from $188M to $0.89M, a 210× drop**, against $2.12bn borrowed. So the tail is not an opportunity that
  rotates between names; it is the same pool, every night, paying an illiquidity premium precisely when a
  supplier cannot withdraw. **The median, not the mean, is the honest estimate of what is safely earnable here** —
  which is the opposite of the carry case, and for a stated reason: there the mean was wrong because the series
  was skewed, here it is wrong because the tail is unreachable.
- **#3 risks, named and not priced.** `aave-v3` USDC/USDT on **Ethereum mainnet** is **contract** exposure —
  smart-contract, oracle and governance risk on Aave v3 — plus two mainnet transactions per round trip.
  `kucoin` (lending-market) and `okx` (flexible-savings) are **custodial** counterparty exposure. Our
  instrument-death work measured CEX perp delistings and **does not transfer to either**. Position cost is
  unmeasured by this collector: gas is not recorded, nor are stablecoin withdrawal fees or the EUR on/off-ramp.
- **#4: the cost is stated first, and the median pool never reaches it.** Raw fee yield across the 129-pool clean
  set (`same_peg`, not yield-bearing, not wrapper, not TVL-implausible), 58,908 observations: **p10 0.000,
  median 0.055, p90 0.904, p99 5.583 bps/day; TVL-weighted 0.230**. Minimum position for gas to fall below 10% of
  *thirty days* of fees, at the median pool: **$122k at $2 gas, $304k at $5, $913k at $15, $2.43M at $40** — and
  **83 of the 129 clean pools are on Ethereum**, the most expensive chain in the set. **The gross does not clear
  the cost, so per the rule #2 taught us nothing here is annualised.**
- **#4's fee yield is one side of a ledger, and the other side is NOT MEASURABLE from this collector.**
  `lp_snapshots` stores APY, TVL and volume — **no pool price, no reserves, no reference price** — so divergence
  loss cannot be computed, and neither can pool staleness against a reference. Our own maker work priced the
  analogous leg on 661,177 real passive fills: **20.87 bps captured at fill, −1.29 bps kept 60 s later.** An LP is
  a passive maker that *cannot cancel*, filled by whoever arrives, including the arbitrageur whose whole purpose
  is to trade against a stale pool price. **A fee yield reported without that leg is exactly the half-a-trade
  error that made carry look like 16.4%.**
- **#4: the yield that exists is concentrated in the pools where the unmeasured risks live.** **75 of 132** clean
  pools are concentrated-liquidity, and they carry **3.2× the yield of the full-range pools (0.097 vs 0.030
  bps/day)** — but out-of-range means holding 100% of one leg, and **in-range time is unmeasurable without pool
  prices**. Only **14 pools are the blue-chip USDC–USDT pair**; the rest pair long-tail issuers — ALUSD, FRXUSD,
  APXUSD, PYUSD, GHO, USDE, FXUSD, REUSD, STRUSD, TRUSD, BOLD, USDG, JUPUSD, HYUSD, MUSD, AUSD, USDF — on
  curve-dex, uniswap-v3/v4, fluid-dex, orca-dex, thalaswap and vvs-standard. **The depeg option is written on
  those issuers specifically.** It is small by construction until the pair stops being stable, then large, and it
  arrives correlated with everything else — orthogonal in normal conditions, correlated in the tail. **Pricing it
  needs a depeg history per issuer (frequency, depth, recovery time) that this collector does not gather.**
- **#4 weekend shape is visible but barely sampled:** weekend median **0.043** vs weekday **0.057** bps/day
  (turnover 0.0345 vs 0.0540) — directionally right for a volume-paid strategy, but the window holds **one
  weekend**, so it is a hint, not a seasonal.
- **Both #3 and #4 collectors are honest, and the daily restart does NOT touch them.** `lending_snapshots`:
  5-min cadence, 34,166 rows over 11.1 d, three gaps >15 min — startup (20 min), the 08-26 power loss (37 min),
  the 09-02 WiFi outage (25 min). `lp_snapshots`: 30-min cadence, 107,373 rows, exactly **one** missed cycle
  (09-02 WiFi, 60 min). **The ~06:10–06:35Z `apt-daily-upgrade` restart punched no holes in either** — 98 rows in
  that window every day except 08-26. Unlike the carry bot, these two reconnect instead of exiting.
  **Inputs sit beside outputs in both**: lending stores `raw_rate` + `raw_basis` (4 kinds) + `conversion_factor` +
  `rate_field` next to the derived `annual_pct` (0% NULL on both); LP stores `tvl_usd`, `volume_usd_1d`,
  `turnover_1d` next to `apy_base`, **and runs its own reconciliation** — `apy_base_vs_volume` agrees on 104
  pools, flags **38 as `base_understated`** and 25 as no-volume. **Applying the #2 lesson: LP's 3.1% NULL
  `apy_base` is a REAL hole, not a derived-column artefact** — 0.0% of it is recoverable from volume and TVL,
  because those are NULL on the same rows. Pool set is stable, 201–212/day, no drift.
- **Candidate #2's first observed settlement (2026-08-28 08:00Z, 27 instruments): the basis is real and the
  trade is not.** This is the first *receipt* for #2 — everything before it was a mark. Held to settlement, the
  trade earns the quoted basis; the quoted basis is tiny and the round trip is not. At the widest horizon the
  collector covers (**4.0 d** — see below), median |basis| **7.6 bps** and median signed basis **+3.2 bps**,
  against a median **held-to-settlement** cost of **17.6 bps** (full spot spread + half the future spread, since
  the future settles rather than being traded out) or **33.1 bps** on the full round trip. **Net is negative at
  every horizon tested** (median −14.4 bps at 4 d, −7.4 to −11.6 bps at 24 h/12 h/6 h/1 h). **7 of 25 instruments
  clear cost before fees; 5 at 1 bps/crossing, 2 at 2 bps, and 0 at 3 bps.** The 2.7–4.5% annualised figure was
  not wrong, it was the wrong quantity: **annualising 3–8 bps over four days manufactures a percentage that the
  spread eats several times over.**
- **Every #2 survivor is BTC or ETH, and the median survivor clears the risk-free anchor by ~1 pp before fees.**
  The 7 that cleared cost at 4 d were bybit/BTC (+3.77 pp over 2.25%), okx/BTC this_week (+4.39), gate/BTC
  (+1.58), okx/BTC this_month (+0.94), okx/ETH this_week (+0.90) — and two that did *not* clear the anchor,
  okx/ETH this_month (−0.43) and bybit/ETH (−1.49). **Median premium +0.94 pp, on nets of 0.8–7.3 bps** — smaller
  than any realistic fee schedule. No alt survived on any venue.
- **This is n=1 in time, not n=2 — the 2026-08-31 "second settlement" does not exist for executable purposes.**
  The settlement calendar shows 08-28 and then **nothing broad until 09-04 08:00Z**; 08-29/30/31 and every day
  between carry **only `deribit:day`, 2 instruments**, which by the venue's own quoting (index vs mark, and
  `roundtrip_spread_bps` NULL on 100% of rows) is not executable. **The repeat that two settlements were supposed
  to buy is not yet available.** It arrives at 09-04 08:00Z (24 instruments). Everything above therefore describes
  one simultaneous event with the venues correlated inside it — **not 25 independent expiries, and not a
  distribution.**
- **Deribit is excluded from every #2 net figure, by construction and not by choice.** `spot_px_source=index`,
  `future_px_source=mid`, `venue_basis_field=estimated_delivery_price`, and `roundtrip_spread_bps` NULL on
  100% of its rows. Its 08-28 and 08-31 observations are recorded as unexecutable marks only: 08-31 BTC/ETH `day`
  showed realised÷marked of 1.06/1.03 at 4 d, which is a well-behaved convergence and still not a tradeable number.
- **The `annualized_pct` NULL below dte 0.5 d is NOT a hole in this analysis — the raw columns cover it.** In the
  final 12 hours `annualized_pct` is 100% NULL (guard `MIN_DAYS_FOR_ANNUAL`), but `basis_bps`, `future_price` and
  `spot_price` are **0% NULL**, and observations run to **2.3 minutes before expiry**. The realised figures above
  are complete, not lower bounds. **Scoped fix, deliberately not made in this session:** the guard should return
  the annualised figure with a `dte`-floor rather than NULL, or the column should be dropped in favour of
  computing it at read time from `basis_bps` and `days_to_expiry`.
- **DECLINE TO MEASURE — the #2-vs-#1 orthogonality test cannot be run in this window, because the funding side
  has no signal.** Over 2026-08-24 → 08-28 the 15 shared coins' mean funding sits between −1.4e-05 and 6.6e-05,
  with **14 of 15 inside [2e-05, 7e-05]** — at or beside the universal `5e-05` rest value. The cross-sectional
  Spearman comes out at **−0.279 (Pearson −0.151, n=15)**, but that is correlation against a near-constant and is
  **not evidence of orthogonality**. Whether #2 diversifies #1 is untestable until funding leaves its default —
  which is the same regime dependence that binds G1.
- **The measured round-trip costs differ from the figures carried forward, and the futures leg still dominates.**
  On the 08-28 cohort: **okx 5.21 bps** (not 23.31), bybit 23.02, gate 19.04. The futures leg is **93–99%** of the
  round trip (bybit 21.34/23.02, gate 18.37/19.04, okx 5.18/5.21), confirming the earlier 92–97% finding.
- **Arbitrage is dead, proven** — mark-price sim 95% win / +22 bps vs honest executable 0.3% win / **−214 bps**
  over 875 trades. Taker crossing cost was the entire loss.
- **8 of 30 ёрш candidates carried manufactured tape, and the selection rule found them by construction** — it
  banded by 24h volume on a venue with a ~$100k reported-volume floor.
- **The ёрш "farm dumb market-makers" premise also failed on measurement, independently of the universe
  problem** — mid-markout was positive on *every* candidate (adverse selection everywhere), and the wide quoted
  spreads are empty: 18–178 ticks against ~$70/min of actual flow, so you are undercut by one tick and there is
  no size to collect.
- **The queue-aware maker-fill simulator was BUILT and RUN (2026-08-15, ~21.7 h of L2, 5 candidates) —
  it is not outstanding work.** Verdict `ERSH_QUEUE_SIM_VERDICT.md`: ёрш is a **queue game, not a latency
  game, and a non-colocated bot loses it.** Front of queue is worth +12 to +14 bps per quoting cycle (LA, ONE);
  the back of the queue — the only place we can actually stand — is **−0.7 to −16.7 bps**. **Latency is
  irrelevant: 50 ms and 1000 ms give the same answer** (LA −0.68 vs −0.85), so a faster link buys nothing.
  Also: **`mexc ONE_USDT` was never locked-1-tick** and should not have been in the candidate set;
  `gate MYX_USDT` is too thin to judge (90.8% of cycles produced no fill).
  **So ёрш has three independent reasons to stay parked, not one.**
- **Maker perp-perp convergence earns ~zero after adverse selection** — measured on **661,177 real passive
  fills**: MEXC coins take a median 20.87 bps half-spread at fill and keep **−1.29 bps** 60 s later. "We earn
  the spread instead of paying it" is empirically false on these books.

**Technical gotchas — all still true**
- MEXC needs `sub.depth.full`; plain `sub.depth` streams unsorted diffs (this made ONE_USDT look like 444 bps
  against a real 15 bps).
- **Markout must be measured from book mid, not trade price** — the sign flipped on all 27 candidates.
- "Locked-1-tick" is time-varying — mexc ONE widened ~6× within a day.
- Trade size is **contracts**; apply the multiplier.
- `/api/carry/export-dataset` is a memory bomb.
- Server work happens only in the SSH panel.
- **Collector semantics changed 2026-08-19T12:59:25Z** (funding-interval fix, and with it the "store inputs
  beside outputs" principle the three new collectors follow). Any analysis crossing that timestamp needs it.
  Depth basket went 129→153 at 2026-08-19T13:28:12Z.
- `LESSONS_LEARNED.md` **does not exist** — the Brain skill `trading-edge-lessons` is the only copy. Recorded so
  the next session stops hunting for it.
- Dead deployment artifacts, ignore: `docker-compose.yml`, `Dockerfile`, `ecosystem.config.js`, `Procfile`,
  `railpack-plan.json`.

## 4. The yardstick — same seven questions for every candidate

Never compare headline APRs. **Report medians and distributions, not means** — the 2× estimator error came from
exactly that.

1. Realised **from receipts**, with the realised ÷ modelled ratio and the epoch count.
2. **Cost ÷ income** on actual turnover.
3. Tail priced: what kills it, base rate, loss given the event.
4. Concentration: instruments needed so one bad event is not a portfolio event.
5. Capacity before the yield degrades.
6. **Net-net as percentage points over ~2.25%**, after cost, tail, tax, off-ramp.
7. Regime stamp — everything so far is one bear-market sample.

**Current scores — nothing yet answers item 1 fully:**

| | #2 basis | #3 lending | #4 LP |
|---|---|---|---|
| 1 realised | partial — 3 Deribit dailies observed to 6 s before expiry, gross only | no — marks | no — source-supplied mark |
| 2 cost÷income | yes for 4 of 5 venues; Deribit 100% NULL | partly — utilisation *is* the cost | no — fee tier on 34% of rows, gas absent |
| 3 tail | no | partly — the 99.97% lockup is observed | no |
| 4 concentration | partly — 136 instruments but only ~14 coins | **no — 6 earnable series, 2 assets** | partly — but the above-anchor tail is 28 pools |
| 5 capacity | no — OI NULL on binance/okx | partly | partly — tail pools are $1–20M, which is the answer |
| 6 net-net | no | no — pinned 3.00–3.65% is visibly near the anchor | no — 0.19% median is below it |

## 5. In flight

| what | state | readable when |
|---|---|---|
| paper carry bot, **generation 26** (2026-09-14 09:25:00Z) | running, paper-mode intact; **first generation that stamps the age of every book it prices against.** Gen 25 fixed the exit path; gens 1–24 could not close at all | **G1 SUSPENDED (§2).** The clean window restarts here; forecast readable 2026-09-28 |
| #2 dated basis (`mexc-basis`) | 5 venues; **08-28 AND 09-04 settlements both observed end to end** | **answered negative, and the repeat confirmed it** — next settlements 09-11 and quarterly 09-25 would add regimes, not change the sign |
| #3 lending (`mexc-lending`) | 5 sources, 14 series, **6 earnable**, 2 assets; 11.1 d, 5-min cadence | **rates already readable** (SE ≤0.15pp on all six); what is NOT readable is regime persistence, and the **USD risk-free comparator is not collected at all** |
| #4 stable LP (`mexc-lp`) | 16 chains, 201–212 pools/day (**129 clean**); 11.1 d, 30-min cadence, 1 weekend | fee side readable now and **below its own gas break-even**; the **adverse leg has no readable date — the collector stores no pool price** |
| database backup | **implemented, verified restorable, running nightly — still on the same disk** | — |

**A DELIBERATE BREAK IN THE RECEIPT SERIES, 2026-09-04.** The bot was stopped at **05:50:33Z** and restarted at
**05:53:14Z** so the basis-leg migration and backfill did not run under a live writer; a second restart at
**06:06:13Z** (generation 18) reconciled the running process with the committed tree, since one cosmetic change
landed after the first. **The running unit's `WorkingDirectory` is `/home/vadym/mexc-trade-bot/researcher` — this
tree, the only clone on the box — so what was committed is what is executing.** A
pre-migration full dump was taken first — **8.01 GB in 289 s**, `/var/backups/mexc/full/trading_bot-20260904T054056Z.dump`,
sha `ea99bf2351…`, TOC-verified, and **local-only**, since the Mac's Remote Login is still off. The 8.9-day window
ends here. **The next analysis must not treat the series as continuous across this point**: before it,
`paper_pnl_usd` means funding-minus-costs; after it, funding-minus-costs *plus* the basis leg. The old series
survives as `funding_only_pnl_usd`, which is what makes the two comparable rather than silently redefined. All 5
open positions survived the restart intact and were re-evaluated on the first risk pass, and their entry marks are
backfilled from `funding_basis_snapshots` and flagged `backfill-median2h`. This is the first window that will
carry a booked basis leg from the start.

**Topology:** home Ubuntu server `trading-server`, reached over Tailscale (100.112.227.114), **systemd
only — no Docker, no nginx, no Railway.** Local PostgreSQL `trading_bot`, role `mexc`.

**Live inventory:** 11 active units — backend, frontend, carry-collector, carry-depth, carry-paper,
venue-funding, ersh-tape, ersh-l2, basis, lending, lp — plus 3 timers (2 backup, 1 diskwatch).
`mexc-researcher` and `mexc-carry-tape` inactive and disabled, both by design.
Row counts **as of 2026-09-04 03:40Z** (they only grow; the stamp is what makes them readable):
`funding_basis_snapshots` +3,135,054 since 2026-08-26 · `paper_carry_events` 559,839 lifetime.

**Generations 11–20, and the correction: the mass restart is NOT daily.** `apt-daily-upgrade` runs unattended
every morning ~06:10–06:35 UTC, but its needrestart step only **restarts every service on the box, PostgreSQL
included**, when a library upgrade actually lands — so the bot loses its database, exits 1, and systemd restarts
it. Measured over the 19 days since the 08-26 reboot, that happened on **exactly three days — 08-27, 09-01 and
09-10** (the fourth stop, 09-04, was the deliberate migration). `apt-daily-upgrade` ran on all the others,
including **09-14 06:22:21Z, 6 minutes before this audit, with no restart.** The earlier "each morning" reading
was wrong; the honest form is **unpredictable and roughly weekly, at a time nobody is watching.** It remains a G5
fact for the same reason. **Generations since the 09-04 migration: gen 18 (09-04 06:06:13Z → 09-10 06:28:34Z,
6.01 d) · gen 19 (09-10 06:28:34Z, died on arrival, exit 1 — PostgreSQL was stopping underneath it) · gen 20
(09-10 06:28:44Z → present, 4.00 d).** `NRestarts=1` again understates: two boundaries. No reboot since
2026-08-26 06:27:49Z (uptime 19 d). Longest clean generation is now **gen 18, 6 d 00:22.**

**ROOT-CAUSED 2026-09-14 — it was never a stall, and it was never 28 hours. It was a crash loop, it has
happened five times, and it totals 55+ hours.** The 2026-09-14 reading ("the main loop stalled for 28.07 h while
the risk thread kept beating") was **wrong in mechanism and understated in scope**. The loop is
**single-threaded**. What actually happened is an exception thrown **part-way through the cycle**:

    cycle():  health() -> check_risk() -> accrue() -> rebalance() -> report()
                 |            |              |
                 +-- risk events written ----+          X never reached

`check_risk` raised on every tick (the `close_group` bug above), so the cycle aborted **after** the risk events
had been written and **before** the accrual was. Hence the signature: risk events continuous with a max gap of
**14.9 min**, accrual and selection silent for over a day, `NRestarts` unchanged, systemd `active (running)`
throughout. **2,554 `cycle failed` lines, all one error**, in five episodes:
**09-05 04:05→15:28 · 09-10 12:04→09-11 19:28 · 09-13 08:51→09-14 06:54 (ongoing at the time of writing)** —
the earlier read caught only the middle one because it dismissed the shorter gaps as normal ~70-min cadence.

**THE DEFECT CLASS, sharpened: it is not "a probe on the wrong thread", it is a liveness signal emitted BEFORE
the point of failure.** Anything that says "I am alive" early in a body of work keeps saying it while the rest of
that work fails. The only signal that cannot lie this way is one derived from the work's **output** — a receipt
written, a selection pass completed. This is the fifth instance of the project's standing defect class (a check
reporting on something other than the thing it is believed to check).

The accounting still survived it: on each resume the bot **caught up every missed epoch at that epoch's own
rate** — 340 receipts, **zero skipped epochs** — so the P&L series is continuous. What was lost is **55+ hours of
selection and exit evaluation** out of a 240-hour window (~23%): no name could be entered, exited or re-ranked.
**This is the G5 item.** An unwatched live book would have been unmanaged for over a day at a time, repeatedly,
with every indicator green — and with an exit it had already decided to take sitting unexecuted.

**FIXED 2026-09-14, in two parts.** (1) The cast, above. (2) **Detection derived from the work itself**
(`app/carry/bot/liveness.py`): time since the last funding receipt was *written* and since the last selection pass
*completed*, evaluated at the **top of every tick, before any work**, so it still runs on a tick whose body
raises. The accrual threshold is **a multiple of the shortest settlement interval in the open book**
(1.25×, so 5 h on a 4 h book) rather than a fixed clock — a missed epoch is loud by construction. Consecutive
cycle failures are counted and escalated instead of logging an identical anonymous traceback ~1,700 times at one
level. **The alarm is proved to fire:** `tests/test_liveness.py` replays the 09-10 episode against a fake clock
and the detector fires **3 minutes in** rather than 28 hours later, and the DB test reproduces the original
`DatatypeMismatchError` so the check demonstrably *can* fail. 22 checks, all passing.

**Carry data coverage 2026-08-26 → 2026-09-04 (8.9 d).** `paper_carry_events`: **no gap over 15 min** after the
08-26 outage — the bot's own receipts are continuous, so the window needs no sub-windowing.
`funding_basis_snapshots` has two: the known 08-26 power loss (05:52→06:30, 38 min) and a **new one,
2026-09-02 21:59→22:24 (25 min) — a WiFi/DNS outage, not a host or service failure** (`wlp3s0`, cloudflared could
not resolve or reach its edge). **The server is on WiFi**; that is the second-largest single point of failure
after the breaker.

**The carry headline, no longer a conditional — both of its conditions have now been met.** It used to read:
*carry earns roughly 20–35% gross APR on deployed capital in the resting regime **if** a position is held past the
~3.5-day break-even, **and if** the unbooked spot–perp basis term is small.* As of this window **both hold**: the
R4 interval fix lets positions run (7 of 7 past 3.5 d, none exited), and the basis leg is booked and measures
**−1.27% annualised, CI [−7.52%, +4.25%] — indistinguishable from zero**, exactly as the conditional hoped. The
realised net is **+23.92% [+15.83%, +32.10%]**. **What replaces the old conditional is a narrower one:** that
number is **76.2% above-default tail** on a book with **zero completed round trips**, and the whole run marked to
liquidation is **+2.72%**. So: *carry clears the USD risk-free rate while the above-default tail persists and if
exits cost what entries cost — and neither has yet been observed end to end in the fixed engine.*

**Carry data coverage 2026-09-04 06:06:13Z → 2026-09-14 06:33Z (10.019 d).** `funding_basis_snapshots`:
**zero gaps over 15 min**, 343,826–348,886 rows/day across 1,197–1,215 names — the marks are well supported.
`paper_carry_events`: the risk stream has **zero gaps over 15 min** (max 14.9 min); the accrual stream has **one,
the 28.07 h main-loop stall above**, which cost no receipts. **Clean accounting window: the whole 10.019 days, one
piece.** **Clean *selection* windows: two — 6.25 d (09-04 06:06 → 09-10 12:00) and 2.60 d (09-11 16:05 →
09-14 06:33)** — so any statistic about opportunity set or rotation must be computed inside those, not across.

**The `max_basis_bps` gate cannot see a regime break.** It tests `abs(basis_mean) > 150 bps` on a *lookback mean*,
so `gate/POWER_USDT` was opened on 2026-09-02 while its basis had been sitting at **−900…−1250 bps for the hour
before entry** — the 14-day mean was still inside the gate. Same defect class as the funding estimator: a mean over
a lookback, applied to a series that had just broken. That single position is now 92% of the window's unbooked
basis mark.

**Carry book today (2026-09-14):** **7 names, $714.01 spot notional, $1,071 deployed**, held
**5.90–24.84 days**. In the whole 10-day window there were **2 opens (09-08: mexc/GUA, gate/INX) and 0 closes** —
nothing has closed since **2026-09-02 08:04Z**. Weights **24.4 / 21.6 / 16.2 / 15.3 / 13.8 / 4.8 / 3.8%**
(largest gate/INX_USDT, against the 6.7% line); **MEXC 71.8%** against the 40% cap. **Zero instrument deaths** —
against an expectation of **0.024** at 12.3%/yr over 7 names × 10 days, so again no information about the death
rate in either direction, and the CI above therefore prices **none** of it.

**THE CURVE-AGE STAMP, shipped 2026-09-14.** `latest_curve` now returns the **age of the curve it served**
alongside the curve (`Curve.ts` / `Curve.age_s`), measured against the **database clock** so a skew between bot
and collector cannot manufacture a fresh-looking book. The **worst (oldest) of the two legs binds** — a position
is only as well priced as its stalest leg — and that figure is stored as **`entry_book_age_s`** and
**`exit_book_age_s`** on `paper_carry_positions`, beside `entry_cost_usd` / `exit_cost_usd`. Inputs beside
outputs, exactly as the basis mark carries `n` / `last_ts` / `source`. Without it an exit priced off a
twenty-minute-old book is indistinguishable, afterwards, from one priced off a live one — and two currently-held
names were already caught in long spot stalls (`mexc/BULLA` 21.0 min, `mexc/H` 15.7 min).
**NO AGE LIMIT IS ENFORCED ANYWHERE, deliberately.** A threshold picked before the distribution is known is
precisely how the depth watchdog came to guard the leg that does not fail. Measure for a few weeks, then set the
limit from the recorded ages. `tests/test_book_age.py` (17 checks) drives the real path against the live books
and proves the value **survives the write**: `open_leg` and `close_group` are executed for real inside a
transaction that is then rolled back, storing **entry 102.283391 s / exit 102.285319 s** and leaving **0 rows**
behind.

**TWO DELIBERATE DEPLOYMENTS, 2026-09-14.**
- **`mexc-carry-depth`, stop 09:24:16Z, running 09:24:18Z** — it had been on the 09-10 process, so the corrected
  `evaluate_stall` was on disk and not in force. **The spot arm is now the binding one**, which was the point: at
  the measured 24 h worst, perp sits at **26%** of its hard limit and spot at **50%** of its own, and
  `evaluate_stall([234], 1350)` returns `soft` on the spot reason while perp alone stays silent. The separate
  limits are not a convenience: **applying perp's 900 s to a 153-symbol REST sweep would have exited the collector
  nine times in one day for stalls that self-recovered, taking healthy perp sockets down with it. A watchdog that
  kills a healthy component to punish a slow one is not a safety feature.**
- **`mexc-carry-paper`, stop 09:24:59.993Z, running 09:25:00.397Z — GENERATION 26.** One clean boundary, no
  failed starts (contrast generations 21-24), **0 `cycle failed` since**. All **8 groups / 16 legs** came through
  with entry marks and provenance intact (2 distinct sources: 6 `backfill-median2h`, 2 `live-median2h`).
  `gate/POWER_USDT` — still the first genuine test of the interval-aware floor, since it is the only 8 h name held
  — sits at **trailing-7 10.5% on capital against the 4.0% floor at 8 h, 0 negative epochs**: healthy, 6.5 pp of
  headroom, and **still untested**, because under the OLD flat 8.0% floor it would already have been exited.

**THE CLEAN WINDOW NOW STARTS AT 2026-09-14 09:25:00Z (generation 26).** It was reset one day after the previous
start, on purpose: **the cost of restarting now is one day of window; the cost of not restarting was fourteen days
of exit data in which no exit could be attributed to a live or a stale book.** Everything before this timestamp
measures a book whose prices carry no age. **Do not pool across it.**

**PRE-COMMITTED FORECAST — restated 2026-09-14 against the new start, readable 2026-09-28.** Unchanged in
substance; only the window moves.

> **Measured over 2026-09-14 09:25:00Z -> 2026-09-28, on completed round trips and open positions marked to
> market, the both-legs net annualised comes in BELOW +15.83% — the lower bound of the withdrawn window's
> interval. If it does not, the tail decay is NOT what sets this yield and something else is supporting it.**

**The decomposition is now three-way and all three must be reported separately, because a fall can come from any
of them and they imply different things:** (1) **realised exit costs** — real, and the first datum
(`gate/INX_USDT`, 8.43 bps against a 17.57 bps provision) says the provision *overstates*; (2) **funding decay** —
the tail thinning, which is the hypothesis under test; (3) **stale-priced exits** — now measurable for the first
time via `exit_book_age_s`, and previously invisible. A total that falls without saying which of the three moved
is not a result.

**WHAT WOULD ACTUALLY CLOSE A POSITION — read 2026-09-14, and the answer is "one already should have".** The
question was whether the exit leg can be measured on this book. Measured state of the seven:
**R4 is FIRING RIGHT NOW on `gate/INX_USDT`** — trailing-7 **7.8%** on capital against the **8.0%** floor,
**−0.20 pp** through it — and has been firing and being denied on every tick since **2026-09-13 08:51Z**. The
next closest are `gate/POWER_USDT` at **+6.40 pp** of headroom and `mexc/HANA_USDT` at **+9.80 pp**. **R5-depth is
healthy on all seven**, none near. **`payback` cannot close anything at all** — it exists only in
`selector.evaluate`, so it is an *entry* gate and was never an exit rule.
**So the correct statement is not "nothing will close for weeks".** It is: **the strategy has already decided to
exit a position and has been physically unable to, for over a day.** **RESOLVED the same day: the fixed code ran
at 07:46:41Z and `gate/INX_USDT` closed 53 seconds later** — see the clean-window entry above. The exit provision
is a provision no longer, and the reason it had been one was a bug rather than a quiet market.

**Blocking, two minutes of work:** the backup ships nowhere. On the Mac — Remote Login ON,
`mkdir -p ~/mexc-backups`, append the `mexc-backup@trading-server` ed25519 key to `~/.ssh/authorized_keys`, then
`SHIP_ENABLED=1` / `SHIP_REQUIRED=1` in `/etc/mexc-backup.conf`. **Until then the backup sits on the disk it
protects.**

**THE UNPRICEABLE EXIT IS A NON-EVENT — measured 2026-09-14, read-only, and no shadow ledger was built.**
The question was what the bot should do when it cannot price an exit, and whether to run a shadow ledger to find
out. **The history answers it without code: the condition has never occurred, and it is currently unreachable.**

- **Base rate: 0 of 101,576 snapshots** across **608 legs** over 6 h had fewer than 3 usable levels. On the eight
  held names over 24 h, **0 of ~20,000**; the thinnest snapshot seen anywhere had **21 usable levels**, against a
  threshold of 3.
- **In all three denial episodes** — `mexc/H` 09-05, `mexc/GUA` 09-10→11, `gate/INX` 09-13→14 — every exit-side
  leg was **fully priceable at every snapshot** (min 21 levels). So during all 2,554 denied exits the book was
  always there. This is the measurement behind the §3 correction.
- **The only way to get an unpriceable leg is a name with no depth stream at all**, and there are exactly **2 of
  153**: `gate/HOODX_USDT` and `gate/AI_USDT`, both missing perp. **Neither has ever been held, and neither can
  be:** `executor.open_carry` requires the same market's curves and refuses to open without them. **Entry and exit
  read the same source, so a name that can be bought can be sold.**
- **Therefore the exit branch is not where the risk lives, and §2's shadow ledger was not built.** Observing an
  event that has never occurred, on a path that structurally cannot reach it, would have been a second engine
  watching nothing.

**WHAT IS REAL IS STALENESS, AND `latest_curve` DOES NOT BOUND IT.** The query takes `max(ts)` for the leg with
**no age limit at all**, so a dead feed does not produce "no price" — it produces **an arbitrarily old price, with
nothing marking it as old**. That is the worse failure of the two, and it is invisible in the way the pre-2026-09-04
basis marks were. Measured over 24 h and 398,450 inter-snapshot intervals:

| | p50 | p95 | p999 | max | >15 min | >60 min |
|---|---|---|---|---|---|---|
| perp (websocket) | 2.01 | 2.11 | 2.84 | **3.9 min** | 0 | 0 |
| spot (REST sweep) | 2.25 | 2.99 | 11.25 | **22.5 min** | 30 | 0 |

**Everything recovered; nothing was ever permanently lost.** All 30 long gaps are **MEXC spot, zero Gate**, and
they cluster in **two correlated bursts** (09-13 12:36 and 09-13 16:27–16:50, 6 and 9 names at once) — the
signature of a sweep stalling, not of per-name market events. **So the ours/market's split is: ours 30, market's
0.** Two currently-held names were caught in it (`mexc/BULLA` 21.0 min, `mexc/H` 15.7 min).

**AND THAT INDICTS THE FIX SHIPPED THE DAY BEFORE.** `carry-depth`'s new hard stall watched the **freshest perp
socket** — and perp's worst gap in 24 h was **3.9 min** while spot's was **22.5 min**. **The escalation watched
the leg that does not fail and ignored the one that does**, which is the standing defect class committed by the
very change meant to close it. `SpotDepthPoller`'s docstring asserted "REST needs no watchdog"; the retry logic is
per symbol and works, but nothing measured **the sweep**, which is what goes slow. Corrected: the poller now
stamps `last_ok_at` on a successful snapshot **write**, and `evaluate_stall` is a pure function judging spot and
perp on **separate limits** — spot soft 900 s / hard 2700 s, deliberately looser, because applying perp's 900 s to
a 153-symbol REST sweep would have exited the collector **nine times in one day** for stalls that self-recovered,
taking healthy perp sockets down with it. `tests/test_depth_stall.py` drives a real poll through `_one` to prove
the stamp moves (and does not move on a failed poll), and asserts the **old perp-only rule returns "ok" during the
real 22.5-minute stall** — the check that can fail. 19 checks. **Not yet deployed: `carry-depth` is still running
the 09-10 process.**

**WHAT THIS IMPLIES FOR ADMISSION AND SIZING, which is where it actually lands.** The exit branch needs no policy
because the condition does not arise; the admission rule is already doing the work, silently and by accident —
`open_carry` refusing a name with no curve is what keeps the two structurally unpriceable names out of the book.
The open exposure is that **an exit can be priced against a book up to ~22 minutes old and nothing records that it
was**. The cheap fix, by exact analogy with the basis mark's `n` / `last_ts` / `source` columns, is to have
`latest_curve` return the **age** of the curve it served and to store it beside `exit_cost_usd`, so exits priced
against stale books are identifiable after the fact instead of being averaged into the exit-cost statistics. That
is a schema change and is **not** done here. **`CARRY_ALLOW_UNPRICED_EXIT` remains a live-policy decision the user
owes — but it now governs a case that has never fired, so it is not urgent.**

**IMBALANCE ANATOMY — Stage 1 and Stage 2 run 2026-09-14, pre-registered in `RESEARCH_IMBALANCE_PREREG.md`
(commit `14a0633`) before any outcome query.** The question behind it: 76.2% of carry income came from the
above-default funding tail and we had never asked whether those episodes have a life.

**Feature inventory — and the decisive feature already exists.** `funding_basis_snapshots` carries
**`perp_open_interest`, 100% populated since 2026-08-20** (26 days, ~5 min cadence, p95 gap 5.06 min), alongside
perp/spot volume, basis, both books' top-of-book sizes, depth5, spreads and marks, over **1,199 names**
(mexc 684 / gate 515). OI is *not* missing; the proposal to scope a collector for it is unnecessary. Also held:
`carry_book_l2` (153-name basket, 50 levels, 2 min), `venue_funding_snapshots` (1,102 names, 4 other venues),
`tape_prints`/`book_ticker` (67 ёрш names only — too narrow to screen 1,199).

**Episode definition (pre-registered, causal).** Resting = `|funding| <= 1e-04` (2x the universal `5e-05`
default). **Onset t=0 = the FIRST crossing of `|funding| >= 2.5e-04` after 24 h wholly at rest** — never the peak,
which is the look-ahead trap. End = return to rest sustained 12 h. **648 episodes, 353 names, 620 completed /
28 censored.** Sign split **456 shorts-crowded (negative) / 192 longs-crowded**, which is the expected direction
in this regime. **179 names first-time-only, 174 recurring.** Venue split is lopsided: **gate 564 episodes,
mexc 84.**

**STAGE 1 — the label conflates TWO ANIMALS, and this is the main descriptive result.** The duration distribution
is **bimodal**: **47.9% of episodes are over within one hour** (median **0.17 h**, and **95.3% of them peak at
t=0 itself**), while the rest run a median **16 h**. A post-hoc cut at 1 h (**declared: not pre-registered**)
separates them cleanly — 297 spikes vs 323 sustained, and 109 names produce only spikes, 133 only sustained,
102 both. **The spike half has no anatomy to study: funding ticks above threshold for one or two 5-minute reads
and returns.** Among the sustained half the shape is **spike-and-decay, not ramp-and-plateau** — the peak falls in
the first quarter of the episode **58.2%** of the time (median at 10.4% of elapsed duration). Peak size, all
episodes: p50 **12.1x** the venue default, p90 **44x**, p99 **349x**.

**Traps, checked.** *Manufactured tape:* the pre-registered volume-CV proxy (declared in the prereg as a proxy
for T9, which needs tape we hold for only 67 names) flags **38 of 1,220 names, every one MEXC, every one with
mean 24 h volume pinned at $67–71k** — the constant-rate emitter signature, reproduced independently. It removes
only **4 of 666 onsets (0.6%)**: manufactured volume is real and is **not** what drives these episodes.
*Survivorship:* **58 of 1,257 names stop reporting** inside the window and are **kept**, per the prereg.
*Multiple testing:* **20 pre-registered hypotheses, Bonferroni alpha 0.0025.** *Regime:* one bear market, stamped.

**STAGE 2 — something does lead t=0, it survives out of sample in time, and it is not what the hypothesis
predicted.** Base rate first: **4.25–4.43% of eligible name-days contain an onset** (eligible = resting for 24 h
with enough history). Of 20 hypotheses, 6 clear Bonferroni in sample. With the direction **fixed on the fit half
(< 2026-09-04)** and measured on the held-out half:

| feature (6 h or 24 h change to t=0) | fit AUC | **test AUC** | top-5% precision | lift vs 4.25% base |
|---|---|---|---|---|
| `svol_6` spot volume | 0.538 | **0.562** | 16.1% | **3.78x** |
| `basis_24` | 0.457 | **0.549** | 13.8% | 3.24x |
| `oivol_6` OI / perp volume | 0.428 | **0.572** | 12.4% | 2.92x |
| `pvol_6` perp volume | 0.568 | **0.556** | 11.4% | 2.68x |
| `ret_6` price return | 0.568 | 0.521 (p=0.27) | — | **FAILED out of sample** |

**Three things must be said with those numbers.** (1) **The effect is small.** An AUC near 0.56 means 84–88% of
the top-5% flagged moments do **not** become onsets. (2) **The direction contradicts the crowd-arriving
hypothesis.** `oivol_6` discriminates *downward* — OI/volume **falls** — while volume rises. That is **churn, not
accumulation**: a crowd arriving would show OI rising faster than turnover. **Open interest on its own is the
weakest of the lot** (`oi_6` AUC 0.477, `oi_24` 0.536), so the pre-registered primary hypothesis is the one the
data least supports, and the ratio's signal comes from its volume denominator. (3) **Near-tautology risk, and it
is the biggest threat to the reading:** funding is struck off the perp premium index, which responds to the same
order flow that produces the volume. "Volume rises in the 6 h before funding crosses" may be mechanically
entailed rather than informative. The statistics are sound; what they *mean* is not settled.

**KILL CRITERION: #4 — a lead survived out of sample in time, so Stage 3 opens; it is NOT begun here, and it must
never be discussed as continuous with carry, because it is a directional position in which principal can be
lost.** The honest size of the prize is a 2.7–3.8x lift on a 4.25% base rate from a 26-day single-regime sample,
with the mechanism unresolved between "early warning" and "arithmetic restatement of the same flow".

**THE SEQUENCING TEST — information, not arithmetic, and the definition of `t_premium` was committed first**
(`RESEARCH_IMBALANCE_ADDENDUM.md`, commit `56519eb`; family widened to 23 hypotheses, Bonferroni
alpha 0.00217). `t_premium` = the first moment in `[t0-24h, t0]` at which `basis_bps` leaves a MAD band built
from `[t0-7d, t0-24h]` — a baseline window that **ends before** the window under test — in the direction of the
funding sign. Exact timestamps, not row offsets, because this is a timing question and the row approximation errs
by minutes.

- **A volume-before-premium window exists.** Of 630 episodes with a usable baseline, the premium moved inside the
  window in **52.1%** and volume in **20.5%**; both moved in **88 (14.0%)**, which is the sequencing sample. In
  **62.5%** of those, **volume moved first**, with a **median lead of 380 minutes** (p25 105, p75 793, p90 1256).
- **And the discrimination sits where the premium has NOT yet moved.** Stratifying every point by whether the
  premium had already left its band at t-6h: **stratum B (premium not yet moved) holds 583 of 588 onsets and
  `svol_6` discriminates there at AUC 0.5611, p=2.9e-06** — significant at the tightened alpha. Stratum A
  (premium already moved) has only **33 points and 5 onsets**, too few to test.
- **Verdict, by the rule committed before the run: INFORMATION, not arithmetic.** With two honest limits: the
  test is **one-sided** — it shows the signal is present before the premium moves, not that it is absent after,
  because stratum A is too small to contrast — and the volume never leaves its own band at all in **79.5%** of
  episodes, so where the lead exists it is real but it is a minority phenomenon.

**THREE THINGS FROM THE STAGE-2 TABLE THAT MUST BE STATED, NOT BURIED.**
1. **`oi_6` has AUC 0.477 — BELOW 0.5.** The direct measure of crowd size is not merely weak, it is **slightly
   inverted**, and it is the **pre-registered primary hypothesis**. This is the single strongest piece of
   evidence against the accumulation story.
2. **`oivol_6` discriminates downward: open interest falls relative to volume.** That is **churn, not
   accumulation** — the same positions changing hands faster, not new ones arriving. **The crowd may not be
   arriving at all.** Stage 3, if it opens, would be testing a different hypothesis from the one that opened it.
3. **The strength is in the volume denominator, not in open interest.** Side by side: **`svol_6` test AUC 0.562**
   (top-5% precision 16.1%, 3.78x lift) against **`oivol_6` 0.572** — and `oi_6` 0.477, `oi_24` 0.536. **Nobody
   may later read the ratio as an OI result.**

**WHERE CARRY'S INCOME ACTUALLY COMES FROM — and it is NOT these episodes.** Joining 958 carry receipts
($+20.2105 lifetime) against the 648-episode catalogue:

| | income | share |
|---|---|---|
| earned INSIDE a catalogued episode | **$+0.1082** | **0.5%** |
| earned OUTSIDE any episode | **$+20.1022** | **99.5%** |

On the above-default component alone the split is the same: **0.6% inside, 99.4% outside.** All 7 inside-receipts
fall in the sustained half; **the spike population contributed exactly $0.0000**, which confirms rather than
assumes that a trailing-window selector is immune to threshold blips.

**The reason is structural and it reframes the whole study.** The episode definition requires **24 h wholly at
rest** before the crossing, so it catalogues **transitions INTO the tail**. Carry's selector ranks on a trailing
window, so it picks names that are **persistently IN the tail** — and such a name can almost never produce an
onset, because it is never at rest. Measured: `mexc/BTW_USDT`, carry's largest earner at **$5.78**, spends
**3.1%** of snapshots at rest and **71.1%** hot, median rate **6.2x** the default — and produced **zero
episodes**. Across the held book the top four earners (BTW, H, HANA, LYN, **$12.3 of $20.2**) produced **zero
episodes between them**, against a universe averaging **78.9% at rest / 7.3% hot**. **The episode catalogue and
carry's income are near-disjoint populations by construction.** The imbalance study does not explain where
carry's money comes from; it studies a different object.

**VENUE AND DIRECTION — and the venue split is mostly an artefact.** gate 564 episodes vs mexc 84 looked like a
venue-behaviour difference. Measured: **gate names sit at rest 86.3% of the time against MEXC's 73.3%** (exactly
at the `5e-05` default 46.2% vs 44.6%, so the default itself is not the difference). **Gate names rest more, so
they far more often satisfy the 24-h-rest precondition an onset requires.** The split is therefore largely **the
definition interacting with each venue's resting behaviour**, not evidence that crowding happens on Gate — and it
dovetails with the paragraph above: MEXC's chronically-warm names are the ones carry holds, which is why carry's
income and the episodes do not overlap. **This does not transfer to venue selection**, which remains a death-rate
question.
**Direction: shorts-crowded 456 vs longs-crowded 192 (2.4:1), inside one bear market** — and negative funding
occupies only **12-15%** of all snapshots, so negative excursions are far more *eventful* than their frequency.
The two halves are **not the same animal**: shorts-crowded episodes are shorter (p50 **1.00 h** vs **2.08 h**)
with slightly smaller peaks. **Most of this income is earned standing on the long side against crowded shorts.
In a bull market the sign flips and the population is a different one, so none of this transfers** — the regime
caveat is now sharper than anything previously written here.

**KILL CRITERION: still #4 — the lead survived out of sample AND survives the mechanism test, so Stage 3 remains
open, on the churn hypothesis rather than the accumulation one that opened it. Stage 3 is NOT begun.**

**Collector stall-detection sweep, 2026-09-14.** Six collectors (`basis`, `bybit`, `carry`, `lending`, `lp`,
`venues`) already derive liveness from **successful writes** and escalate: soft-stall rebuilds the HTTP session,
hard-stall exits for a clean systemd restart. **`carry-depth` logged stale-socket age and never escalated** — and
that is the collector whose silence makes `close_carry` price an exit against a stale book. It now hard-stalls and
exits when the **freshest** perp socket exceeds 900 s (soft warning at 300 s; no forced reconnect, because each
socket already self-reconnects and inventing a second unverified path in that file is how it got into trouble the
first time) — **and, after the 2026-09-14 measurement below, when the spot sweep exceeds 2700 s on its own looser
clock, which is the side that actually stalls.** The original claim that this collector's silence *causes* an
unpriceable exit is withdrawn: it causes a STALE curve, never a missing one, because `latest_curve` has no age
bound. **`ersh-tape` and `ersh-l2` have no stall detection at all — recorded
and left, the ёрш line is closed.**

**Standing watch:** the listing-obligation hypothesis is **untested, not refuted** — the earlier "refuted" call
was an artifact (all 30 ёрш symbols' first prints fall inside a 273.5 s window = collector start). Testable
prospectively via `research/queries/new_listing_watch.sql`.

**Data-gap rule:** `INTERMITTENT` fires on all ~1,197 symbols for any lifecycle audit whose window is under
~11 h and spans the 2026-08-26 gap (~5 h for the 08-24 gap). `DISAPPEARED` cannot misfire — it needs
`last_seen > 24 h`. **Any lifecycle audit must exclude the gap windows or use a window longer than ~11 h.**

**Known collector defects, unfixed and worth knowing before they cost anything:** `annualized_pct` is NULL for
100% of basis observations at dte < 0.5 d — i.e. absent through the convergence window you most want;
`roundtrip_spread_bps` is NULL for 100% of Deribit and 82% at dte 0.5–2 d, so cost is missing where the trade
goes on; **Deribit prices index vs mark, a modelled pair, so it has no executable number at all**; binance and
okx have `venue_basis_raw` and `future_oi` 100% NULL.

## 6. Parked — with the reason, so it is not re-proposed

- **Gate↔MEXC arbitrage** — −214 bps over 875 executable trades.
- **Listing event edge** — negative even with perfect direction foresight at +5m.
- **Delisting convergence** — real and systematic, but a one-sided perp discount, not neutral convergence.
- **Fast funding-floor exit rule** — 8:1 against; ~60% false exits on the population it would fire on.
- **ёрш / maker spread collection** — universe invalid at the selection step; would need a rule that does not
  band by reported volume.
- **Multi-venue as diversification** — same factor everywhere.

## 7. Working rules

- **A NEW CHECK MUST BE REPLAYED AGAINST A RECORDED REAL FAILURE, NEVER AN IMAGINED ONE.** Proven twice in one
  week, and both times the replay found something reasoning had missed. The liveness detector was replayed
  against the **2026-09-05** episode, not only the 09-10 one it was written for — and that is what showed the
  3.63 h hole sits *under* the accrual limit and is caught only by the selection and failure arms. The depth rule
  asserts that the **old perp-only test returns "ok" during the real 22.5-minute stall**, which is what
  demonstrated the shipped watchdog guarded the leg that does not fail. A check validated against an invented
  scenario validates the imagination, not the system. **Corollary, learned from generations 21-24: the replay
  must drive the code path, not construct the object.** A smoke test that built `CarryBot` passed while
  `seed_liveness` was broken.

1. **Every prompt names the gate it serves in its first line** — G1…G5, or explicitly `FIX` / `HOUSEKEEPING`.
   A prompt serving no gate and not labelled a fix is drift.
2. **Every prompt's final step: update this file** — which gate moved, what became settled, what changed in §5.
   The side holding the data maintains the plan; this is also the bridge between chats.
3. **Session sign-off names the gates *not* touched.** This is the anti-drift mechanism.
4. **Fixes are labelled and bounded** — say whether the break *blocks a gate*. The rest is where drift lives.
5. **No dated snapshot documents.** A dated document is written only when a gate closes — that is a verdict.
6. **Project memory holds durable facts and rules only, never current state.** State lives in §5 and is edited.
7. **Brain is for "what happened" and cross-chat catch-up, not the plan.**
