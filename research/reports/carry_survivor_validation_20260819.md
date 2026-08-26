# Carry survivor validation — identity, traps, authenticity, death risk

**Date:** 2026-08-19 · **Analyst:** Claude (session on `trading-server`)
**Scope:** §2 (symbol identity) and §3 (survivor triage) of prompt-56. Both **READ-ONLY**.
**Source:** `funding_basis_snapshots` 7.87M rows + live venue metadata.

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days) during a bear market — BTC
topped $126,199 in Oct 2025 and traded near $60,000 in mid-2026, a >50% drawdown. Thin-name
liquidity is depressed market-wide in this window. One regime, one sample.**

*No capital discussion appears in this document. The survivor list is not actionable until it
is both authenticity-gated and depth-sized; neither is true yet.*

---

## Lead findings

**§3a — 7 of the 45 survivors are names an earlier pass explicitly rejected as traps, and they
occupy ranks 1, 2 and 3.** `mexc/BTW` (135.8%), `gate/龙虾` (127.5%) and `gate/AI` (71.5%) were
all on the published trap list. On inspection the re-admissions split three ways, and **2 of
them are re-admitted wrongly because my survivor gate has no funding-reversal test** — the
exact criterion the original trap analysis used to reject them.

**§3c — all 45 survive their own death risk, and it is not close.** Loss given death, measured
from the 16 observed deaths, is 3.33% of notional centrally and 72.4% in the p95 tail. Against
a 12–24%/yr venue death probability that costs at most 17.6 percentage points, so the weakest
survivor still clears +12.6% after a tail-sized death charge. **Death is not what is being
compensated here.**

**§2 — venue metadata cannot detect the perp≠spot mismatch.** 12 of 1,197 pairs are
mismatched, and **10 of the 12 have base-asset metadata that agrees on both legs.** The tokens
genuinely differ while both venues honestly label them with the same ticker. Price ratio is the
only signal that works.

**§3b — 42 of 45 survivors (93.3%) have no authenticity evidence at all**, and of the 3 that do,
**2 are SUSPECT**.

---

## §2 — Symbol identity across all 1,197 pairs

### Method

Three independent signals, not the join string:

| leg | identity field available |
|---|---|
| MEXC perp | `baseCoin`, `quoteCoin`, `settleCoin`, `baseCoinId` (`contract/detail`) |
| MEXC spot | `baseAsset`, `quoteAsset` (`exchangeInfo`) |
| Gate spot | `base`, `base_name`, `quote` (`spot/currency_pairs`) |
| Gate perp | **none** — `futures/usdt/contracts` exposes no base-asset field |

Plus a price-ratio sanity band on the median `perp_mark / spot_price` over the full window
(median, so a transient dislocation cannot move it):

`OK` |ratio−1| ≤ 0.02 · `WARN` ≤ 0.10 · `FAIL` > 0.10 · `SEVERE` ratio > 2 or < 0.5

### Results

| verdict | gate | mexc | total |
|---|---|---|---|
| OK | 517 | 658 | **1,175** |
| METADATA_UNVERIFIED | 3 | 18 | 21 |
| SUSPECT (WARN band) | 1 | 4 | 5 |
| **MISMATCH** | **8** | **4** | **12** |

**Of the 45 survivors, zero fail** — all 45 are `OK`. The |basis| ≤ 200 bps filter applied when
the survivor set was built already removed every mismatched pair. That is reassuring about the
survivor list and says nothing good about the method (below).

| pair | n_obs | median ratio | median basis bps | base metadata agrees? |
|---|---|---|---|---|
| mexc/EWT_USDT | 6,597 | **372.689** | 3,716,890 | **yes** |
| gate/GUA_USDT | 6,598 | 0.264 | −7,357 | **yes** |
| gate/OPENAI_USDT | 6,598 | 1.586 | 5,863 | **yes** |
| mexc/ESPORTS_USDT | 6,597 | 0.451 | −5,494 | **yes** |
| gate/TQQQX_USDT | 6,598 | 0.498 | −5,017 | **yes** |
| gate/SIREN_USDT | 6,598 | 0.589 | −4,106 | **yes** |
| gate/SPCX_USDT | 6,598 | 1.160 | 1,602 | **yes** |
| mexc/SIREN_USDT | 6,597 | 0.853 | −1,469 | **yes** |
| gate/RAVE_USDT | 6,598 | 0.868 | −1,322 | **yes** |
| gate/ESPORTS_USDT | 6,598 | 0.897 | −1,034 | **yes** |
| gate/VANRY_USDT | 4,476 | 1.987 | 9,873 | no (delisted, metadata absent) |
| mexc/VANRY_USDT | 4,525 | 1.123 | 1,228 | no (delisted, metadata absent) |

### Is the failure systematic or one-off? — Neither, and that is the finding

**It is not a naming-convention error, and metadata cannot fix it.**

`mexc/EWT_USDT` is the clean demonstration: perp `baseCoin = "EWT"`, spot `baseAsset = "EWT"`,
both `quote = USDT`. **Metadata says the legs match.** The perp trades at $105.88 and the spot
at $0.262 — a **404× ratio**. Two different tokens share the ticker `EWT`, and each venue leg
labels its own token correctly. There is no string bug to fix.

**Detection rate of the metadata check on the 12 mismatches: 0.** (The two flagged
`base_meta_match = False` are the delisted VANRY pair, where metadata is simply absent — not a
detection.) The 10 live mismatches all pass every metadata test available.

A second structural gap: **Gate's perp contract endpoint exposes no base-asset field at all**,
so on Gate the "metadata" check degenerates to re-parsing the same contract name we joined on.
For 525 of 1,197 pairs there is no independent perp-side identity to check.

### Proposed root fix (NOT implemented, per instruction)

A basis threshold is a band-aid because it cannot distinguish a mismatched pair from a
genuinely dislocated one — `gate/SIREN` at ratio 0.589 could be either, and this study cannot
tell you which.

1. **Resolve identity outside the venue's ticker namespace.** Bind each leg to a
   chain-level identifier — token contract address, or a cross-referenced external asset id —
   fetched from each venue's asset/currency endpoint, and require the two legs to resolve to
   the same identifier. This is the only fix that addresses the actual failure mode.
2. **Persist a continuous ratio monitor as a first-class field**, not an analysis-time filter:
   store the median ratio and flag a pair when it leaves a band, so a mismatch is caught when
   it appears rather than when someone next runs a study.
3. **Interim, cheap:** treat `|median ratio − 1| > 0.10` sustained over ≥24h as a hard exclusion
   at universe-build time, and log it rather than silently dropping.

Estimated blast radius today: **12 of 1,197 pairs (1.0%)**, 5 more in the WARN band. None are
in the survivor set.

---

## §3a — Trap cross-check (lead item)

The published trap list (`CARRY_CANDIDATES.md`, "Traps — high gross APR that fails the gates")
names 12 symbols. **7 of them are among the 45 survivors:**

| survivor | rank | net APR | original rejection reason | flips/wk **now** | assessment |
|---|---|---|---|---|---|
| mexc/BTW_USDT | **1** | 135.81% | spot spread 21 bps | **0.00** | **legitimately re-admitted** |
| gate/龙虾_USDT | **2** | 127.49% | **3.68 reversals/wk** | **4.83** | **STILL A TRAP — gate failed** |
| gate/AI_USDT | **3** | 71.52% | both spreads >23 bps | **0.00** | **legitimately re-admitted** |
| gate/TRUST_USDT | 11 | 56.66% | perp spread 23.5 bps | **0.00** | **legitimately re-admitted** |
| gate/TUT_USDT | 14 | 55.77% | **3.68 reversals/wk** | **3.63** | **STILL A TRAP — gate failed** |
| gate/POWER_USDT | 16 | 55.09% | reversals; spot 21; basis −68 | 1.20 | borderline |
| gate/ONE_USDT | 28 | 41.85% | 2.21 reversals/wk; spot 21 | 1.80 | borderline |

The other 5 trap names (mexc STAR, BROCCOLIF3B, ARCSOL, ON, CLO) were **not** re-admitted —
all fail the ≤50 bps cost gate (56.8–101.6 bps), i.e. the spot-spread objection that killed them
originally still kills them.

### What changed, per category

- **3 legitimately re-admitted** (BTW, AI, TRUST). These were rejected on a spread threshold
  (~20 bps/leg) applied against an APR that was **understated 2×**. At the corrected APR the
  same spread is a materially different proposition — BTW earns 135.8% against a 24.2 bps
  round trip. Nothing about the name changed; the arithmetic it was judged against did.
- **2 still traps** (龙虾, TUT). Both were rejected for *funding reversals*, not spread, and
  both still reverse at 4.83 and 3.63 flips/wk. **My survivor gate does not test reversals.**
  It uses `pct_cycles_positive ≥ 90`, which is a *level* test: 龙虾 is positive in 92.1% of
  epochs while flipping sign 16 times. A name can be positive most of the time and still be
  uncarryable.
- **2 borderline** (POWER, ONE) at 1.20 and 1.80 flips/wk — improved on their original 1.47
  and 2.21, but not to zero.

### Consequence — a gate defect this cross-check exposed

**The survivor gate is missing a reversal criterion.** Adding `flips_per_week < 1.0` would
remove 7 of the 45 (龙虾, TUT, ONE, POWER, mexc/GUA, mexc/IDOL, mexc/AKE) and specifically
both names identified above as still-traps. Across all 45, flips/wk has median **0.00**, with
only **7/45 ≥ 1/wk** and **2/45 ≥ 3/wk** — so the defect is narrow, but it sits at ranks 2 and 14.

**Answer to the brief's conditional:** a majority are *not* re-admitted traps — 7 of 45 (16%),
of which 2 are genuine gate failures. But they are top-of-list, so the headline number is
sensitive to them.

---

## §3b — Authenticity overlap

| | count |
|---|---|
| survivors | 45 |
| tape-screened symbols (total) | 30 |
| **survivors with tape evidence** | **3** |
| **survivors with NO authenticity evidence** | **42 (93.3%)** |

Of the 3 with evidence: `gate/ONE_USDT` **CLEAN**, `mexc/BLUAI_USDT` **SUSPECT**,
`mexc/LAB_USDT` **SUSPECT`.

**Expressed as the brief requires — a fraction of the screened overlap: 2 of 3 (67%) of
screened survivors are SUSPECT.** That is n=3; it is a ratio over three observations, not a
rate, and it must not be read as "67% of the survivor list is suspect".

*Correction to the brief's premise:* it names three SUSPECT survivors (BLUAI, LAB, CASHCAT).
**`mexc/CASHCAT_USDT` is not in the 45** — it fails the gate on `pct_cycles_positive` 78.1
(<90) and breakeven 4.4 days (>3). Only 2 SUSPECT names are in the survivor set.

**The honest statement: the authenticity status of 42 of the 45 is unknown.** `tape_prints`
covers the ~30 ёрш names and nothing else, so the screen has never been pointed at this
population. High funding on a thin name is precisely the profile where fabricated volume was
found, so this is the largest unquantified exposure in the survivor list.

---

## §3c — Death-adjusted APR (lead item)

### Every assumption, printed beside the number

| assumption | value | source | weakness |
|---|---|---|---|
| P(death) annual, MEXC | **24.3%** | lifecycle audit, 12 deaths / 684 symbols / 23 d | 23-day window, bear regime |
| P(death) annual, Gate | **11.3%** | lifecycle audit, 4 deaths / 529 symbols / 23 d | only 4 events |
| LGD central | **3.33%** of notional | median exit round-trip spread at death (277.6 bps) + median \|basis\| at death (55.2 bps), n=16 | assumes you can exit at the quoted spread |
| LGD tail (p95) | **72.38%** of notional | p95 exit spread + p95 \|basis\|, driven by gate/VANRY's 6,564 bps terminal basis | single observation drives it |
| leverage | **1× (unlevered, per unit notional)** | — | a levered book scales the loss |

`death_adjusted_APR = net_APR − P(death) × LGD`

### Subpopulation rate — tested, then rejected as underpowered

The brief asks for a subpopulation rate if n permits. It does not:

| cohort | n | deaths | annualised |
|---|---|---|---|
| top-decile \|funding\| | 122 | **3** (gate/HIGH, gate/VANRY, mexc/VANRY) | 32.4% |
| whole universe | 1,213 | 16 | 18.9% |

32.4% vs 18.9% is *suggestive* that high-funding names die faster, but **k=3**. A 95% Poisson
interval on 3 events spans roughly 0.6–8.8 events, i.e. 7%–80% annualised. **The venue rate is
used instead, and this is stated wherever the number appears.**

### Result

| | survivors clearing 0% |
|---|---|
| at LGD central (3.33%) | **45 / 45** |
| at LGD tail (72.38%) | **45 / 45** |

**The headline figure the brief asks for: 45 of 45 survive their own death risk.**

Weakest five after the tail-sized charge:

| symbol | net APR | tail-adjusted | P(death) used |
|---|---|---|---|
| mexc/ACU_USDT | 32.58% | **14.99%** | 24.3% |
| mexc/EVAA_USDT | 32.44% | **14.86%** | 24.3% |
| mexc/M_USDT | 32.27% | **14.68%** | 24.3% |
| mexc/AKE_USDT | 31.46% | **13.87%** | 24.3% |
| mexc/VELVET_USDT | 30.20% | **12.61%** | 24.3% |

Even charging every MEXC name a 24.3% annual probability of a 72.4%-of-notional loss — an
aggressive pairing, since the 72.4% comes from one Gate name — the worst survivor keeps +12.6%.

**Interpretation.** Token death is real (12.3%/yr batch-adjusted) but it is *cheap* relative to
this level of carry, because a delisting is announced and the median exit costs ~3.3% of
notional, not the position. **Death is not the risk being compensated at 30–136%.** Of the five
candidate explanations the brief lists, this analysis prices death and finds it small, and
finds perp≠spot mismatch absent from the survivor set. **That leaves fabricated volume and
missing depth as the unpriced candidates — and both are exactly what §3b shows is unmeasured
for 93% of the list.**

---

## §3d — Sample honesty

**Correction to the brief's premise.** It states "23 days is ~6–7 settlements for an 8h
symbol". It is ~69: 22.96 days × 3 settlements/day. Measured:

| interval | survivors | settlements observed |
|---|---|---|
| 8h | 6 | **70** |
| 4h | 39 | **139** |

Median across all 45: **139**. Minimum: **70**. The sample is roughly **10× larger than the
brief assumed**, and a 136% APR here rests on 139 independent settlements, not six.

Sign flips over the window:

| | value |
|---|---|
| median flips/week | **0.00** |
| survivors with ≥1 flip/wk | 7 / 45 |
| survivors with ≥3 flips/wk | **2 / 45** (gate/龙虾 4.83, gate/TUT 3.63) |
| survivors with 0 flips across all settlements | 33 / 45 |

33 of 45 never printed a negative funding rate in 70–139 settlements. That is a genuinely
strong stability record for this window — with the caveat that the window is one bear-market
regime, and funding persistence is regime-dependent.

---

## What I could not determine, and why

1. **Whether the 42 unscreened survivors carry real volume.** This is the single largest gap.
   `tape_prints` covers only the ~30 ёрш names, so the authenticity screen has never been run
   on this population. The 2-of-3 SUSPECT rate on the screened overlap is over three
   observations and cannot be extrapolated.
2. **Whether any survivor has tradeable depth.** `perp_depth5_usd` and `spot_depth5_usd` are
   NULL in every historical row. Bid/ask *sizes* began being collected at 12:59Z today
   (Gate perp and MEXC spot only — the venues do not expose the other two legs in bulk), so
   depth evidence starts accruing now and did not exist for this analysis. **An APR without a
   size is not a business, and no survivor currently has a size.**
3. **Whether `gate/SIREN`-class pairs are mismatched or genuinely dislocated.** At ratio 0.589
   the price test cannot separate "different token" from "perp trading 40% below spot for real
   reasons". Only a chain-level identifier settles it, which is exactly why the §2 fix is
   proposed rather than a threshold.
4. **A stable death rate for thin high-funding names.** k=3. The point estimate (32.4%) is
   nearly double the universe rate and would matter if true, but the interval is uselessly wide.
5. **Whether LGD is exit cost or the whole position.** LGD here assumes the position can be
   exited at the observed terminal spread. If a delisting force-closes at a settlement price
   with the basis dislocated — the gate/VANRY 6,564 bps case — the loss is the dislocation, not
   the spread. I priced that as the tail, but with n=1 the tail shape is unknown.
6. **Regime.** One 23-day bear-market window. Funding persistence, delisting cadence and thin
   liquidity are all plausibly regime-dependent, and there is no second regime in this dataset
   to test against.
7. **Whether the reversal gate should be 1.0/wk.** I identify the missing criterion but the
   threshold is chosen from the original trap analysis's own cut, not fitted or validated here.

---

## Deliverables

- `research/reports/data/carry_survivors_risk_adjusted.csv` — 45 rows, every assumption as a column
- `research/reports/data/symbol_identity_check.csv` — 1,197 rows, all identity signals + verdict


---

# AMENDMENT (prompt-56a) — 2026-08-19

## A1. §3c correction — which LGD produced +12.6%, and the mean

**The +12.6% figure came from the TAIL, not the central estimate.** It was also computed on a
tail number that was itself wrong.

**Arithmetic error, corrected:** the original "tail LGD = 72.38%" was built as
*p95(exit spread) + p95(|basis|)* — two separate 95th percentiles added together. That is not
the 95th percentile of anything. The p95 of the LGD **distribution** is **66.52%**.

### LGD across the 16 observed deaths, side by side

| statistic | value | note |
|---|---|---|
| **mean** | **7.56%** | **the correct input for an expected-value calculation** |
| median | 3.64% | what the original report used as "central" |
| p75 | 5.96% | |
| **p95** | **66.52%** | **rests on a single observation (gate/VANRY, 6,564 bps terminal basis)** |
| max | 66.52% | same observation |
| mean excluding gate/VANRY | 3.63% | n drops 16 → 15 |

**One death supplies 52% of the mean.** Removing gate/VANRY moves the mean from 7.56% to 3.63%.
With n=16 and the tail resting on n=1, the mean is the right EV input *and* is unstable.

### death_adjusted_APR recomputed on the MEAN

`death_adjusted_APR = net_APR − P(death) × LGD_mean`, with P = 24.3% (mexc) / 11.3% (gate).

| | survivors clearing 0% |
|---|---|
| at **mean** LGD (7.56%) | **45 / 45** |
| at p95 LGD (66.52%) | **45 / 45** |

Weakest on the mean: `mexc/VELVET_USDT` 30.20% → **28.36%**. Weakest on the corrected p95:
`mexc/VELVET_USDT` 30.20% → **14.04%** (the earlier report said 12.61%, from the erroneous 72.38%).

The conclusion is unchanged — all 45 clear on every LGD statistic — but the drag is smaller
than the original "central" number implied: **1.84 pp/yr** on a MEXC name, not 0.81.

## A2. When does a p95 death stop being a line item?

A p95 death loses **66.52% of that position**. Subtracting it from an APR is the wrong mental
model, because it does not arrive as a drag — it arrives at once, on one name.

| names held (equal weight) | weight/name | portfolio loss from ONE p95 death | reads as |
|---|---|---|---|
| 3 | 33.3% | 22.17% | **portfolio event** |
| 5 | 20.0% | 13.30% | **portfolio event** |
| 8 | 12.5% | 8.32% | material drawdown |
| 10 | 10.0% | 6.65% | material drawdown |
| 15 | 6.7% | 4.43% | line item |
| 30 | 3.3% | 2.22% | line item |
| 45 | 2.2% | 1.48% | noise |

**A p95 death is a portfolio event (≥10%) at any concentration above ~6.7% per name — i.e.
fewer than ~7 names equally weighted.** It falls below a 2% line item only under ~1.3% per name,
around 33 names.

And it is not rare. P(at least one death per year) among *k* held names:

| | k=3 | k=5 | k=10 | k=20 | k=45 |
|---|---|---|---|---|---|
| mexc (24.3%/yr) | 56.6% | 75.1% | 93.8% | 99.6% | 100.0% |
| gate (11.3%/yr) | 30.2% | 45.1% | 69.9% | 90.9% | 99.5% |

**The asymmetry an APR subtraction cannot express:** mean LGD × PD = 1.84 pp/yr is a *drag* and
subtracting it is correct. The p95 at 66.52% — **8.8× the mean** — is a single event on a single
name, and no per-annum subtraction represents it. Both numbers are needed; neither replaces the
other.

*Every figure here rests on n=16 deaths with the tail defined by n=1 (gate/VANRY).*

**Update from §7:** all 16 deaths are now **CONFIRMED_DELISTING** by direct venue probe (Gate
`in_delisting: true`; MEXC ticker returning no `data`). 11 of 16 had a spot pair still trading.
The LGD numbers therefore describe genuine perp-contract delistings — the forced-deadline event
the model assumes — and not a data artifact or a spot-side exit.

## A3. §2 carried forward — a pre-trade gate, not an analysis filter

**There is currently no reliable pre-trade identity check.** Every metadata test available
detected **0 of 12** mismatches; `mexc/EWT` labels both legs `EWT` correctly and is still two
different tokens at a 404× price ratio; and Gate's perp endpoint exposes no base-asset field at
all for 525 of 1,197 pairs.

**Requirement to carry into the eventual bot, recorded now while the reason is fresh:**

> Until chain-level identifiers are wired in, the price-ratio sanity band is the **only** working
> mismatch detector, and it must be a **hard pre-trade gate evaluated at entry time on every leg
> pair** — not an analysis-time filter applied to a ranking. Reject any pair whose
> `|median(perp_mark / spot_price) − 1|` exceeds 0.10 over a trailing window, and re-evaluate at
> entry rather than trusting a ranking computed earlier.

Rationale: a ranking is a snapshot. The mismatch that matters is the one present at entry, and
a pair can decouple between the ranking and the trade. An analysis filter cannot catch that; a
pre-trade gate can.

## A4. §3a generalised — level tests cannot see oscillation

The §3a finding deserves stating as a project-wide rule rather than a fact about 龙虾:

> **`pct_cycles_positive ≥ 90` is a LEVEL test and is blind to OSCILLATION.** `gate/龙虾_USDT`
> is funding-positive in 92.1% of epochs while flipping sign 16 times (4.83/week). A threshold
> on a level says nothing about the path taken to it.

**Every gate in this project should now be audited for the same blind spot** — anywhere a
criterion is expressed as "fraction of observations above X", ask what the path looked like.
Known instances to re-check: the carry selection gates, the ёрш spread-regime conditioning
("locked-1-tick" is already documented as time-varying), and the R4-funding-flip risk rule in
the paper bot (which uses a trailing APR level, not a reversal count).

## A5. §4 decision applied — reversal gate is a COLUMN, not a filter

Per the decision, all 45 are retained and instrumented. `reversal_gate` is recorded per symbol
and **defaults to showing only PASS** in any ranking or shortlist; the 7 FAIL names are kept for
measurement and must never be selected from.

| reversal_gate | n | names |
|---|---|---|
| **FAIL** (flips/wk ≥ 1.0) | **7** | gate/龙虾 (4.83), gate/TUT (3.63), gate/ONE (1.80), gate/POWER (1.20), mexc/GUA (1.21), mexc/IDOL (1.21), mexc/AKE (1.21) |
| PASS | 38 | all others (median flips/wk 0.00) |

The 7 are the only evidence that can tell us whether 1.0/wk is the right threshold. Dropping
them now would make the gate permanently unvalidatable — the same trap the authenticity veto
rule fell into with HOODRAT. After §5 collection, the test is whether the 7 differ from the 38
on depth and tape authenticity: if they are also thin or fabricated, the reversal gate is
partly redundant; if they look identical on the new instruments, it is doing independent work
and stays.
