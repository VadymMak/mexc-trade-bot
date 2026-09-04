# BLUEPRINT — the plan

**This file is the plan. Everything else is appendix.** Edited in place, never appended to; history lives in git.
If something here is stale, fix it here — do not write a new status document.

Last updated: 2026-09-04 · Supersedes `CURRENT_STATUS.md`

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
- **Status:** open. **The basis leg is now BOOKED (2026-09-04) — the instrument is fixed, the answer is not.**
  A carry position earns funding *and* the change in the spot–perp basis; until this date only the first existed
  in the record (`close_price` was NULL on every closed leg). Both are now booked, mid-to-mid with execution cost
  kept as its own line, and the 46 historical positions are backfilled and flagged. **The 16.4% net APR stays
  WITHDRAWN.** Booked over the whole run: funding-only **−$2.2711**, basis **+$0.7459**, total **−$1.5251**. Over
  the 14-position post-churn-fix window: funding-only **+$2.0981**, realised basis **−$0.2993**, total
  **+$1.7988** — and the open book carries **−$2.26 unrealised**, of which **−$2.42 is gate/POWER_USDT alone**
  while the other four net **+$0.16**. Marked to market the window is **−$0.46**. The honest statement is
  unchanged: the carry P&L **is not distinguishable from zero**, and the reason is now measured rather than
  suspected. What the window *can* still support is a conditional, in §3.
  The window did **not** supply a second
  regime: median funding across 1,190–1,203 names sat at exactly the `5e-05` default on every one of the 10 days,
  55–64% of names at or below it, no trend; BTC 78.6k → 80.9k (+3.0%, range 77.2–80.9k). This is 9 more days of
  the resting regime, so the gate's actual question is untouched.
  Two things make the 16.4% fragile rather than reassuring: **57.5% of the income came from 3 names held
  continuously for the whole window** (BTW, LYN, H) and 72.9% from the 4 still open — the rotating names
  contributed 21.9%; and **the opportunity set thinned monotonically inside the window** — names passing all gates
  9.8 → 4.7/day, top-ranked modelled APR 76% → 38%, with `payback` rejections rising 29 → 51 as `trail7`
  rejections fell 104 → 80.
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
- **Status:** all three collecting since 2026-08-24. **First look done — and it reorders the candidates.**
  - **#2 dated basis is the only one that does not rest at a default.** Median 2.7–4.5% annualised, tight across
    five independent venues — the tightness across venues *is* the evidence it is a real price.
  - **#3 lending rests at defaults, same signature as funding** — kucoin and okx supply rates have **one distinct
    value in 611 observations**. Only 6 of 14 series are the supply side you can actually earn.
  - **#4 LP median is 0.19%, below the risk-free anchor**, with a 9× mean/median ratio; only 14.55% of
    observations clear 2.25%.
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
- **Carry engine cost ÷ income is 87.9% on COMPLETE round trips, not 27%.** The 27% was measured over 21.5 h on
  positions whose exit cost had not yet been booked; it was a partial-lifecycle number. On the 14 round trips that
  opened *and* closed after the Phase-3b fixes: income $5.69, cost $5.00, net **+$0.69**. The engine fixes are real
  (rebalances 491/day → 3; 0 errors) — the cost ratio was not.
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
- **Durability:** `data_checksums=on`, `fsync=on`, `full_page_writes=on`; **0 checksum failures for the cluster's
  whole life.** Corruption is not the risk; the single copy is.
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
| paper carry bot, **generation 17** (2026-09-04 05:53:14Z) | running, paper-mode intact; **first generation that books the basis leg** | continuously; G1 needs a second regime, not more days |
| #2 dated basis (`mexc-basis`) | 5 venues, 136 instruments, 40 expiries | **narrow now**; broad multi-venue convergence **2026-08-28**; 4–6 cycles ≈ **2026-10-02**; first quarterly **2026-09-25** |
| #3 lending (`mexc-lending`) | 5 sources, 14 series, 2 assets | Aave mean to ±0.05pp ≈ **2026-09-12**; **CEX series are constants — no date** |
| #4 stable LP (`mexc-lp`) | 13 chains, ~203 pools (116 clean) | first full Mon–Sun **2026-08-31**; 30-day mean ≈ **2026-09-23** |
| database backup | **implemented, verified restorable, running nightly — still on the same disk** | — |

**A DELIBERATE BREAK IN THE RECEIPT SERIES, 2026-09-04.** The bot was stopped at **05:50:33Z** and restarted at
**05:53:14Z** (generation 17) so the basis-leg migration and backfill did not run under a live writer. A
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

**Generations 11–16, and why there are six of them.** `apt-daily-upgrade` runs unattended each morning ~06:10–06:35
UTC and its needrestart step **restarts every service on the box, PostgreSQL included** — so the bot loses its
database, exits 1, and systemd restarts it. That is the whole explanation for gens 11→12 (2026-08-27 06:12) and
12→13/14/15/16 (2026-09-01 06:29–06:33), and for `NRestarts=1` understating six generation boundaries. No reboot
since 2026-08-26 06:27:48Z. **This is a G5 fact, not a curiosity: an automated job stops a running strategy weekly,
at a time nobody is watching, and the machine has no UPS.** Longest clean generation is **gen 12, 5 d 00:17**.

**Carry data coverage 2026-08-26 → 2026-09-04 (8.9 d).** `paper_carry_events`: **no gap over 15 min** after the
08-26 outage — the bot's own receipts are continuous, so the window needs no sub-windowing.
`funding_basis_snapshots` has two: the known 08-26 power loss (05:52→06:30, 38 min) and a **new one,
2026-09-02 21:59→22:24 (25 min) — a WiFi/DNS outage, not a host or service failure** (`wlp3s0`, cloudflared could
not resolve or reach its edge). **The server is on WiFi**; that is the second-largest single point of failure
after the breaker.

**The carry headline, written as the conditional it actually is.** The window supports no level, only this:
*carry earns roughly 20–35% gross APR on deployed capital in the resting regime **if** a position is held past the
~3.5-day break-even, **and if** the unbooked spot–perp basis term is small.* **The first condition is currently
prevented by R4; the second is unknown and is the larger of the two.** Everything downstream — the 87.9% cost
ratio, the weight-cap table, the R7 valuation — is denominated in funding-only P&L and inherits that second
unknown. **Booking the basis leg at exit is the single highest-value fix in the project**, because until it exists
no carry number means anything; it is also cheap, since `close_price` is already a column and the marks already
exist in `funding_basis_snapshots`.

**The `max_basis_bps` gate cannot see a regime break.** It tests `abs(basis_mean) > 150 bps` on a *lookback mean*,
so `gate/POWER_USDT` was opened on 2026-09-02 while its basis had been sitting at **−900…−1250 bps for the hour
before entry** — the 14-day mean was still inside the gate. Same defect class as the funding estimator: a mean over
a lookback, applied to a series that had just broken. That single position is now 92% of the window's unbooked
basis mark.

**Carry book today:** 5 names, $857 spot notional, $645 of $1,080 capital deployed (59.7%, down from 100% on
08-26 as exited names could not be replaced). 9 closes in the window, **8 of them R4-funding-flip**, 1 R5-depth
collapse; 6 opens. **Zero instrument deaths** — against an expectation of **0.05** at the measured 12.3%/yr base
rate over 17 names × 9 days, so this observation carries no information about the death rate in either direction.

**Blocking, two minutes of work:** the backup ships nowhere. On the Mac — Remote Login ON,
`mkdir -p ~/mexc-backups`, append the `mexc-backup@trading-server` ed25519 key to `~/.ssh/authorized_keys`, then
`SHIP_ENABLED=1` / `SHIP_REQUIRED=1` in `/etc/mexc-backup.conf`. **Until then the backup sits on the disk it
protects.**

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

1. **Every prompt names the gate it serves in its first line** — G1…G5, or explicitly `FIX` / `HOUSEKEEPING`.
   A prompt serving no gate and not labelled a fix is drift.
2. **Every prompt's final step: update this file** — which gate moved, what became settled, what changed in §5.
   The side holding the data maintains the plan; this is also the bridge between chats.
3. **Session sign-off names the gates *not* touched.** This is the anti-drift mechanism.
4. **Fixes are labelled and bounded** — say whether the break *blocks a gate*. The rest is where drift lives.
5. **No dated snapshot documents.** A dated document is written only when a gate closes — that is a verdict.
6. **Project memory holds durable facts and rules only, never current state.** State lives in §5 and is edited.
7. **Brain is for "what happened" and cross-chat catch-up, not the plan.**
