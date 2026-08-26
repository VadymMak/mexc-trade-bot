# Fake-liquidity / wash-trade screen on our own tape (T9)

**Date:** 2026-08-19 · **Analyst:** Claude (read-only session on `trading-server`)
**Sample:** `tape_prints` 2026-08-13 13:35 UTC → 2026-08-19 11:57 UTC, 142.4 h, 2,407,428 prints, 30 symbols
**Status:** READ-ONLY. No schema change, no writes, no service touched.

> **Bottom line:** 8 of 30 symbols are disqualified as manufactured tape. Five of them
> (`mexc` AURASOL, INDEX, KET, WISHBONE, FRONG) each produce almost exactly **$100k of
> reported 24 h volume**, and our tape captures **~100.0%** of it — there is no organic
> flow underneath. The ёрш flagship `gate/LA_USDT` **passes**. The known-toxic
> `mexc/HOODRAT_USDT` **is caught**, but only after the screen was rebuilt — the first
> version scored it CLEAN. That failure and its fix are documented in §6.

---

## 1. Regime stamp (applies to every number in this report)

Collected during a **bear market**: BTC topped at $126,199 in Oct 2025 and traded near
$60,000 in mid-2026, a >50% drawdown. Thin-name liquidity is depressed market-wide in
this window, so organic flow looks sparser and burstier than it would in other
conditions. **All findings describe this regime only.** In particular the burstiness and
tail thresholds (§5) are calibrated on bear-market flow and would need re-fitting in a
higher-activity regime.

---

## 2. Data, units, and sanity checks

### 2.1 Tables

| table | rows | symbols | window | note |
|---|---|---|---|---|
| `tape_prints` | 2,407,428 | 30 | 08-13 13:35 → 08-19 11:57 | `ts` = **exchange event time**, not insert time |
| `book_ticker` | 1,741,411 | 30 | same | written only on BBO **change**, throttled to ≤1/s/symbol |
| `ersh_book_l2` | 9,347,620 | 5 | 08-14 16:26 → 08-19 11:57 | 5 levels/side; `size_usd` already multiplier-adjusted |

Columns are as documented — `tape_prints(id, ts, exchange, symbol, price, size, side)`.
`side` is never null and never anything but `buy`/`sell` across all 2.4M rows.

### 2.2 Coverage gaps — quiet market vs dead collector

Total minutes with **zero prints across all 30 symbols: 17**, out of 8,542 minutes
(99.80% uptime). They form one contiguous outage, **2026-08-15 16:20–16:35 UTC**. This
is the ~1000 s maximum inter-arrival gap that appears in *every* symbol's gap
distribution, confirming it is a service outage and not a market event.

No other symbol-level silence is attributable to the collector, so per-symbol quiet
periods below are genuine market quiet. Per-symbol hour coverage (of 143 hours):
26 symbols have all 143; the exceptions are genuinely thin names —
`gate/WEN` 139, `gate/SKDD` 142, `gate/CRDO` 131, `gate/KIOXIA` 106, `gate/SPCH` 91.

### 2.3 CRITICAL: the contract-multiplier conversion

`tape_prints.size` is in **CONTRACTS**. All size statistics below are computed on
**base-coin units = size × multiplier**, and notional on **price × size × multiplier**.

Multipliers fetched live from `GET /futures/usdt/contracts` (Gate `quanto_multiplier`)
and `GET /api/v1/contract/detail` (MEXC `contractSize`), the same sources
`researcher/app/core/contract_specs.py` uses. **All 30 symbols resolved — zero exclusions.**
They span 0.01 → 100, so `price × size` would be wrong by up to 4 orders of magnitude
and would be wrong *differently per symbol* — i.e. it would corrupt cross-symbol
comparison, which is the entire point of this screen.

**Worked example — `gate/LA_USDT`, multiplier 10.0:**

| ts (UTC) | price | size (contracts) | × 10.0 = base coin | notional USD |
|---|---|---|---|---|
| 2026-08-19 11:58:03.211 | 0.05552 | 48 | 480.00 | 26.6496 |
| 2026-08-19 11:58:01.210 | 0.05549 | 63 | 630.00 | 34.9587 |
| 2026-08-19 11:57:31.641 | 0.05549 | 42 | 420.00 | 23.3058 |

**Independent verification.** `ersh_book_l2.size_usd` is written by the running L2
collector, which applies its own multiplier lookup. Back-solving
`size_usd / (price × size)` over the last 2 h reproduces the multipliers I fetched, with
standard deviation **exactly 0.000000000** on all five overlapping symbols:

| symbol | implied | fetched | n |
|---|---|---|---|
| gate/BMT_USDT | 10.000000 | 10.0 | 28,840 |
| gate/LA_USDT | 10.000000 | 10.0 | 59,350 |
| gate/MYX_USDT | 1.000000 | 1.0 | 17,060 |
| gate/ONE_USDT | 10.000000 | 10.0 | 29,900 |
| mexc/ONE_USDT | 1.000000 | 1.0 | 24,780 |

### 2.4 Side-decoding validation

The decode (MEXC `push.deal` `T=1`→buy / `T=2`→sell; Gate `futures.trades` signed size)
was re-verified against the prevailing book. **This test is only valid where the
reference quote is fresh**, because `book_ticker` only records BBO *changes*: on symbols
whose book rarely moves, the "prevailing quote" can be minutes stale and the comparison
is meaningless. Restricting to prints with a quote **< 2 s old**:

| symbol | side | n | % at/above ASK | % at/below BID |
|---|---|---|---|---|
| gate/LA_USDT | buy | 8,874 | **62.0** | 5.2 |
| gate/LA_USDT | sell | 9,577 | 4.0 | **63.5** |
| mexc/ANSEM_USDT | buy | 703 | **75.2** | 7.5 |
| mexc/ANSEM_USDT | sell | 985 | 1.8 | **89.1** |
| mexc/JIMOTHY_USDT | buy | 1,711 | **84.2** | 7.8 |
| mexc/JIMOTHY_USDT | sell | 1,742 | 11.8 | **81.9** |
| mexc/ENJ_USDT | buy | 58 | **94.8** | 0.0 |
| mexc/ENJ_USDT | sell | 50 | 2.0 | **94.0** |

**The decode holds.** Full-window side-correctness reaches 0.87 (`mexc/LAB`), 0.85
(`JIMOTHY`), 0.81 (`ANSEM`, `HOODRAT`) — see `fw_side_decode_correct` in the CSV.

Where side-correctness is *low* it is not a decode bug but the finding itself: on
FRONG/WISHBONE/KET/INDEX/AURASOL only 3–5% of prints touch either side of the quote,
so there is no side to be correct about (§4.3).

**Collector audited, not assumed.** I read `app/ersh/mexc_tape.py` and `app/ersh/store.py`
to rule out our own pipeline as the source of the anomalies in §4. The collector is a
pure websocket consumer: `add_print` is called only from `push.deal`, there is no
polling, no synthetic or heartbeat row, and `ts` is the exchange's own event timestamp
(`ms_to_dt(d["t"])`), falling back to `now()` only when the exchange omits it. **The
periodicity reported in §4.2 is in MEXC's timestamps, not ours.**

`market_flow.py` was **not modified** — it retains the known `sub.depth` bug and feeds
running services. Left alone, as instructed.

---

## 3. NBER-style detectors

### 3.1 Benford's law — reported, but it does not discriminate here

Chi-square vs Benford on the first significant digit of converted size rejects
conformity at **p < 0.001 for all 30 symbols**, including every symbol independently
confirmed clean. This is the well-known large-*n* failure: at n = 5k–277k, chi-square
detects deviations far too small to matter. Nigrini's MAD criterion is no better —
every symbol exceeds the 0.015 "nonconformity" threshold.

The deeper reason is that **Benford's scale-invariance assumption is violated by
construction**: exchange lot granularity truncates the low end and position limits
truncate the high end, so trade sizes do not span the orders of magnitude Benford
requires.

| digit | Benford | gate/BMT | gate/LA | mexc/FRONG | mexc/WISHBONE | mexc/HOODRAT |
|---|---|---|---|---|---|---|
| 1 | 30.1 | 35.4 | 25.9 | 23.6 | 31.8 | **61.4** |
| 2 | 17.6 | 16.8 | 7.8 | 28.1 | 2.7 | 9.4 |
| 3 | 12.5 | 11.1 | 6.7 | 27.4 | 6.7 | 6.0 |
| 4 | 9.7 | 7.0 | 8.0 | 11.7 | 10.3 | 9.6 |
| 5 | 7.9 | 8.7 | **29.0** | 5.3 | 10.5 | 4.7 |
| 6 | 6.7 | 11.0 | 6.5 | 2.9 | 10.4 | 3.9 |
| 7 | 5.8 | 4.2 | 7.0 | 0.6 | 9.7 | 2.7 |
| 8 | 5.1 | 3.1 | 4.5 | 0.2 | 9.3 | 1.9 |
| 9 | 4.6 | 2.7 | 4.5 | 0.3 | 8.6 | 0.5 |
| **MAD** | — | 0.0231 | 0.0496 | 0.0609 | 0.0460 | 0.0694 |
| **p** | — | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

Note `mexc/WISHBONE` — a *disqualified* symbol — has a **flatter, more Benford-like**
histogram than clean `gate/LA`. Benford is worse than useless as a standalone flag here.

**Decision: Benford carries weight 5 of 100 and is barred from vetoing.** Reported for
completeness and because it was asked for; not trusted. The chi-square implementation
was validated against textbook critical values (χ²=15.507, df=8 → p=0.050;
χ²=20.090 → 0.010; χ²=2.733 → 0.950).

### 3.2 Size rounding and concentration

Modal-size share on converted sizes. This catches one failure mode well and **misses
its mirror image entirely** — see §4.4.

Strongest concentration: `mexc/HOODRAT` 59.5% at one size (100 base coin = the 1-contract
minimum), `gate/CRDO` 58.6% at 0.01, `gate/SKDD` 46.1%, `gate/SPCH`/`gate/WEN` 40.1%,
`gate/ONE` 25.9%, `gate/LA` 21.6% at 530.

### 3.3 Tail

Hill estimator on the top decile of converted sizes. Organic flow is heavy-tailed
(low α); a truncated or absent tail is the synthetic signature.

Organic range observed: α ≈ 0.86–2.3 (`gate/ONE` 0.86, `gate/LA` 1.17, `gate/BMT` 1.52).
Synthetic: `mexc/AURASOL` **30.6**, `mexc/WISHBONE` 8.7, `mexc/INDEX` 7.6, `mexc/KET` 6.9,
`mexc/FRONG` 4.6 — i.e. effectively no tail at all.

### 3.4 Inter-arrival regularity

Coefficient of variation of gaps between prints. Organic flow is bursty (CV ≫ 1);
a machine cycling inventory is too even.

A cluster of seven MEXC symbols sits at **CV ≈ 0.51–0.60**, i.e. *more regular than a
Poisson process*. Their gap quantiles are decisive:

| symbol | p05 | median | p95 | mean | CV |
|---|---|---|---|---|---|
| mexc/FRONG | 5.998 | 10.999 | 15.999 | 10.754 | 0.54 |
| mexc/WISHBONE | 5.998 | 10.998 | 15.999 | 10.736 | 0.54 |
| mexc/KET | 5.998 | 10.998 | 15.999 | 10.740 | 0.53 |
| mexc/INDEX | 5.999 | 10.999 | 16.000 | 10.739 | 0.54 |
| mexc/ENJ | 5.999 | 11.000 | 16.000 | 10.821 | 0.53 |
| mexc/AURASOL | 6.000 | 11.000 | 16.000 | 10.961 | 0.51 |
| *gate/LA (control)* | 0.000 | 1.120 | 35.970 | 4.849 | **2.97** |

That is **Uniform(6, 16) seconds** — five different assets drawing inter-trade times
from an identical uniform distribution. Real trades do not arrive that way. Since the
timestamps are the exchange's own (§2.4), this is a property of the venue's tape.

It also explains the ~47,000-print cluster noted at extraction:
142.4 h × 3600 ÷ 10.75 s ≈ 47,700 prints, matching FRONG 47,674, WISHBONE 47,736,
KET 47,719, INDEX 47,724 and ENJ 47,360.

### 3.5 Price-level clustering

Share of prints at the top-5 price levels (`fw_px_top5_share` in the CSV). This turned
out to be the **weakest** detector: it is dominated by tick size and price level, not by
authenticity. `gate/LA` shows 8.7% and `mexc/AURASOL` 17.0%, but `gate/MYX` (clean)
shows 15.9% and `gate/SPCH` 17.2%. **Weight 5, barred from vetoing**, same as Benford.

---

## 4. Our own additions

### 4.1 Order-flow sign persistence — a strong detector the paper could not run

Real order flow is **sign-persistent** (order splitting and herding), so consecutive
prints alternate side *well below* 50% of the time. A synthetic stream drawing sides
from a fair coin alternates at exactly 50%.

| group | symbols | alternation |
|---|---|---|
| organic | gate/CRDO 9.2%, gate/OKB 13.0%, gate/KOMA 14.4%, gate/ONE 15.1%, mexc/ANSEM 14.2%, mexc/JIMOTHY 15.6%, mexc/HOODRAT 16.8%, gate/LA **26.2%** | 9–26% |
| **coin-flip** | mexc/WISHBONE 50.9%, FRONG 51.1%, INDEX 51.2%, KET 51.2%, AURASOL 53.0%, CATI 53.2% | **51–53%** |
| **anti-persistent** | mexc/ENJ | **72.1%** |

ENJ at 72% is *deliberately alternating* — further from organic than a coin flip.
This detector carries weight 20.

### 4.2 Tape vs venue-reported 24 h volume — the single most damning result

Our observed tape notional per 24 h vs the venue's own reported 24 h volume
(Gate `volume_24h_settle`, MEXC `amount24`), fetched live:

| symbol | observed $/24h | venue reported $/24h | obs/rep | verdict |
|---|---|---|---|---|
| mexc/KET | 102,153 | 102,054 | **1.001** | DISQUALIFIED |
| mexc/WISHBONE | 100,941 | 100,364 | **1.006** | DISQUALIFIED |
| mexc/FRONG | 102,240 | 101,537 | **1.007** | DISQUALIFIED |
| mexc/AURASOL | 98,914 | 100,128 | **0.988** | DISQUALIFIED |
| mexc/INDEX | 100,731 | 103,387 | **0.974** | DISQUALIFIED |
| mexc/HOODRAT | 117,942 | 107,636 | 1.096 | DISQUALIFIED |
| gate/LA | 1,128,729 | 2,137,629 | 0.528 | CLEAN |
| gate/AAOI | 3,137,120 | 4,023,856 | 0.780 | CLEAN |
| gate/BMT | 1,369,123 | 427,010 | 3.206 | CLEAN |

Two things at once:

1. **Five unrelated assets each report almost exactly $100,000 of 24 h volume.** That is
   a *target*, not a market — the round number one would pick to satisfy a listing
   requirement or a market-making contract volume floor.
2. **obs/rep ≈ 1.000 on all five.** Our single websocket feed captures ~100% of the
   venue's entire reported volume. On genuine names the ratio scatters widely (0.39–3.35)
   because a 6-day mean and a venue's trailing-24h window disagree as real volume
   fluctuates. A ratio pinned at 1.000 across five symbols means the stream we see **is**
   the entire reported volume, with no organic flow underneath it.

### 4.3 Print-vs-book consistency

For each print, the prevailing BBO (< 2 s old, else excluded as unpowered). A genuine
taker **must cross** to the bid or the ask; a self-trade need not.

| symbol | fresh n | crossed | strictly inside | median spread bps |
|---|---|---|---|---|
| mexc/AURASOL | 1,947 | **0.033** | 0.967 | 232 |
| mexc/INDEX | 8,449 | **0.042** | 0.958 | 382 |
| mexc/KET | 6,320 | **0.051** | 0.949 | 263 |
| mexc/WISHBONE | 5,431 | **0.051** | 0.949 | 337 |
| mexc/FRONG | 12,924 | **0.053** | 0.947 | 373 |
| gate/LA | 61,425 | 0.656 | 0.344 | 9.8 |
| gate/BMT | 112,540 | 0.845 | 0.155 | 12.4 |
| mexc/JIMOTHY | 66,705 | 0.927 | 0.073 | 31.4 |

**95% of prints on the five suspect names occur strictly inside a 230–380 bps spread.**
That combination — an enormous quoted spread with all "trading" happening in the middle
of it, never touching either side — is the classic self-trade signature, and it means
the quoted spread on these names is decorative. This detector carries the highest
weight (25).

**A caveat I want on the record:** `nomove_share` (prints not followed by a book change)
is *confounded* by book-update rate, because `book_ticker` only logs BBO changes. A
symbol whose book rarely moves mechanically shows high `nomove_share` whether or not
anyone is cheating. I computed it, found it non-separating once that confound is
accounted for, and **excluded it from the score**. The crossed/inside split above is
the same evidence without the confound.

### 4.4 Size *uniformity* — the mirror-image failure the NBER rounding test misses

The rounding test (§3.2) looks for *concentration*. Three disqualified names have almost
none — `mexc/KET` top-1 share 0.20%, `mexc/ENJ` 0.21%, `mexc/WISHBONE` 1.13% — and look
pristine by that measure. Their sizes are drawn **uniformly from a small discrete grid**:

| symbol | distinct sizes | top-5 shares |
|---|---|---|
| mexc/AURASOL | **58** | 6.72, 6.68, 6.68, 6.67, 6.66% |
| mexc/INDEX | **88** | 4.51, 4.40, 4.39, 4.37, 4.36% |
| mexc/FRONG | **194** | 2.94, 2.90, 2.90, 2.89, 2.87% |
| gate/LA *(organic)* | 1,354 | 21.59, 2.99, 2.47, 1.99, 0.94% |

A flat top-5 is `random.choice()` over a fixed list. Organic size distributions are
steeply decaying. **Uniformity index** = top-1 share × distinct sizes (≈1 for a uniform
grid, ≫1 for organic) separates the population cleanly:

| band | symbols |
|---|---|
| **< 10 (synthetic)** | ENJ 3.4, WISHBONE 3.4, AURASOL 3.9, INDEX 4.0, FRONG 5.7, KET 9.6 — **all six disqualified** |
| 15–30 (thin, ambiguous) | WEN 15.7, SPCH 29.3 — both INCONCLUSIVE |
| > 35 (organic) | CATI 35.4 … gate/ONE 4180 |

Every symbol below 10 is disqualified on independent grounds, and this metric catches
ENJ and KET, which §3.2 rated clean. **It is reported as a diagnostic
(`fw_uniformity_index`) but is NOT in the composite score** — it was discovered during
this run and has not been validated out-of-sample. It is my top recommendation for the
next iteration.

### 4.5 Cross-venue cross-check — ONE_USDT on both venues

Same asset, same 142 h window, same collector, different venue:

| | gate/ONE_USDT | mexc/ONE_USDT |
|---|---|---|
| crossed share (worst day) | 0.820 | **0.297** |
| alternation | 0.161 | **0.467** |
| CV inter-arrival | 4.45 | **0.48** |
| Hill α | 0.86 | 0.56 |
| worst-day score | **81.3** | **48.8** |
| verdict | CLEAN | SUSPECT (veto: burstiness) |

The underlying asset is identical, so the divergence cannot be a property of ONE as a
market. It is a **venue-specific artifact**: on MEXC, ONE's tape is metronomic and
coin-flip-sided; on Gate the same asset trades burstily with persistent flow. This is
the cleanest available demonstration that the screen measures venue behaviour rather
than asset behaviour — and it is a direct warning about `mexc/ONE_USDT`, which is on the
ёрш locked-1-tick candidate list.

---

## 5. The composite score

### 5.1 Weights — stated so you can disagree

Each component is scored 0–100 (100 = authentic) by linear interpolation between an
"obviously fake" and an "obviously organic" anchor, then weighted:

| component | weight | 0 at | 100 at | rationale |
|---|---|---|---|---|
| `book_cross` | **25** | crossed ≤ 0.10 | crossed ≥ 0.60 | most direct evidence; a real taker must cross |
| `size_conc` | **20** | top-1 ≥ 0.60 | top-1 ≤ 0.10 | brief calls rounding the strongest single flag |
| `sign_persist` | **20** | alternation ≥ 0.50 | alternation ≤ 0.28 | coin-flip sides cannot be organic |
| `burstiness` | **15** | CV ≤ 0.60 | CV ≥ 1.50 | organic flow clusters |
| `size_tail` | **10** | α ≥ 8.0 | α ≤ 3.0 | truncated tail = synthetic |
| `price_disp` | 5 | top-5 px ≥ 0.50 | ≤ 0.10 | weak, tick-size dominated |
| `benford` | 5 | MAD ≥ 0.09 | MAD ≤ 0.03 | non-discriminating here (§3.1) |

Components with insufficient data are dropped and the remainder re-normalised, so a
symbol is never penalised for a test that could not run.

### 5.2 Scoring window — worst day, not average day

**Detectors are recomputed per UTC day and the verdict is driven by the WORST scored
day**, with `score_latest_day` and `score_full_window` reported alongside. A maker
strategy has to survive the bad days, not the average day. §6 shows why this is not
optional.

A day needs ≥ 500 prints to be scored; the book test needs ≥ 200 fresh quotes.

### 5.3 Veto rule

Any **veto-eligible** component (`book_cross`, `size_conc`, `sign_persist`,
`burstiness`, `size_tail`) scoring **< 10** on the binding day is a veto:
one veto caps the verdict at SUSPECT, two or more force DISQUALIFIED.

This exists because a weighted mean lets one screaming detector be averaged away by six
quiet ones — precisely the HOODRAT failure in §6. `benford` and `price_disp` are
**barred from vetoing**, because §3.1 and §3.5 show they do not discriminate; letting
them veto produced three false positives (`gate/LA`, `gate/MYX`, `gate/BSP` were all
wrongly demoted to SUSPECT on a Benford veto alone before this restriction).

### 5.4 Buckets

| bucket | rule | justification |
|---|---|---|
| **CLEAN** | worst-day ≥ 70 and no veto | the observed organic population runs 74.8–95.9 with a floor at 70 |
| **SUSPECT** | 45 ≤ worst-day < 70, or exactly one veto | the empirical gap: nothing sits between 41.7 and 45.3 |
| **DISQUALIFIED** | worst-day < 45, or ≥ 2 vetoes | all members independently confirmed by §4.2 volume evidence |
| **INCONCLUSIVE** | no day reached n ≥ 500 | stated as "inconclusive, n = X", never rounded into a verdict |

Thresholds are set at **observed gaps in the data**, not chosen a priori: the CLEAN
cluster bottoms out at 74.8 (`gate/LA`), and there is a clean break between
`mexc/CATI` 41.7 and `mexc/BLUAI` 45.3.

---

## 6. Reconciliation against what we already knew — including a screen failure

### 6.1 HOODRAT — the screen failed first, then was fixed

The ёрш detector previously flagged `mexc/HOODRAT_USDT` at **75.6% size
concentration**; it proved to be minimum-size dust and the most toxic name in the
sample (markout +57 bps, ~−30% drift).

**My first composite scored HOODRAT 84.0 = CLEAN.** Per the brief, that means the
screen was wrong. The diagnosis:

**HOODRAT has two different pathologies at two different times, and a 6-day mean splits
the difference into "healthy".**

| day | prints | distinct sizes | top-1 share | modal size | crossed | mean spread bps |
|---|---|---|---|---|---|---|
| 08-13 | 66,816 | 194 | **73.8%** | 100 | 94.7% | 57 |
| 08-14 | 41,954 | 189 | **78.4%** | 100 | 97.4% | 50 |
| 08-15 | 21,659 | 187 | 51.5% | 100 | 92.9% | 80 |
| 08-16 | 17,413 | 217 | 38.1% | 100 | 78.8% | 89 |
| 08-17 | 8,505 | 158 | 3.6% | 100 | 18.5% | 221 |
| 08-18 | 8,230 | 214 | 1.6% | 6000 | 9.3% | 291 |
| 08-19 | 4,024 | 115 | 1.9% | 4000 | **4.0%** | **376** |

- **08-13/14 — dust spam.** 73.8%/78.4% of prints at exactly 100 base coin (the
  1-contract minimum). This **reproduces the ёрш 75.6% figure**, confirming both
  measurements. But these prints *did* cross the book (95%), so the book detector saw
  nothing wrong.
- **08-17/19 — fake book.** The dust stopped, the spread blew out 57 → 376 bps, and
  prints moved inside it (crossing 95% → 4%). Now `book_cross` screams but `size_conc`
  sees a healthy 1.9%.

Neither pathology is present on every day, so **every individual detector looks
acceptable on average** and the mean lands at 75.0. Two fixes were required:
per-day scoring with a **worst-day** verdict, and the **veto rule** so a single
component at 0 cannot be averaged away.

**After the fix: HOODRAT = DISQUALIFIED**, worst day 08-19 score **25.6**, with four
vetoes (`book_cross` 0.0, `sign_persist` 0.0, `burstiness` 0.0, `size_tail` 0.0). Its
full-window score of 75.0 is retained in the CSV as evidence of exactly this trap.

I would not have caught this without the known-bad reference name. That is an argument
for keeping a labelled control in every future screen.

### 6.2 gate/LA_USDT — the flagship passes, with one caveat

| | value |
|---|---|
| verdict | **CLEAN** |
| worst day | 2026-08-16, score **74.8** |
| latest day | 2026-08-19, score **92.9** |
| full window | 93.7 |
| vetoes | none |

Worst-day components: `book_cross` 84.0 (crossed 52.0%), `sign_persist` 100 (alternation
19.2% — strongly persistent, organic), `burstiness` 100 (CV 2.30), `size_tail` 57.3
(α 5.14), `size_conc` 59.3, `price_disp` 20.6.

Supporting evidence: obs/rep volume ratio 0.528 (we see about half the venue's reported
volume — the healthy pattern, unlike the pinned 1.000 of the fakes); side decode 59.4%
correct with a clean buy/sell split; median spread 9.8 bps; uniformity index 292.

**Trend is improving**, not degrading: daily crossed 39% → 59% → 46% → 52% → 62% → 71% →
67%, with the spread tightening 38 → 10 bps.

**The one caveat:** 21.6% of LA prints are at exactly **530 base coin** (53 contracts).
That is not a round clip and not the minimum — it is one participant's fixed size. It
does not trip any veto and the rest of LA's distribution is organic (1,354 distinct
sizes, uniformity index 292), but if the maker simulator assumes it can post inside that
flow, **it is competing with a single sized bot, and the 530-lot is the thing to model
explicitly.** LA passes; go in with that known.

---

## 7. False-positive risk — read before dropping anything

**A thin, genuinely illiquid market can look mechanical without anyone cheating.** This
is the failure mode that costs us candidates, and it is real here:

1. **Small-sample instability.** CV and Hill α need a few hundred observations. On a
   quiet day a real market may produce 200 prints and score erratically. *Mitigation:*
   days with n < 500 are not scored; symbols where **no** day reaches 500 are
   **INCONCLUSIVE**, never disqualified. `gate/SPCH_USDT` (999 prints total, 91 of 143
   hours) and `gate/WEN_USDT` (2,302 prints, 139 hours) land there. They are **not**
   cleared and **not** condemned — the honest answer is *inconclusive, n = 999 and
   n = 2,302 respectively*. To resolve them we need a longer window, not a stronger prior.

2. **Thin ≠ fake, and the two look alike on three of seven detectors.** A quiet market
   has few distinct sizes, few price levels and a short tail — scoring badly on
   `size_conc`, `price_disp` and `size_tail` for entirely innocent reasons. Note WEN
   (15.7) and SPCH (29.3) sit just above the synthetic band on uniformity index. This is
   why `book_cross` carries the most weight: **a quiet honest market still crosses the
   spread when it trades.** Crossing rate is the most volume-robust discriminator here.
   It fires on 7 of the 8 disqualified names. **The exception is `mexc/CATI_USDT`**
   (`book_cross` 19.6, crossed 19.8%), disqualified on `sign_persist` + `burstiness`
   alone — it is the weakest of the eight disqualifications and the one most likely to
   be a thin-market false positive. CATI also has the best trend of the group
   (crossed 24% → 82% over the window). **If any disqualification is revisited, revisit
   CATI first.**

3. **Bear-market regime compression.** Every threshold in §5.1 is calibrated on
   depressed 2026 liquidity. In a busier regime organic CV and tail would rise, and the
   current anchors would be too lenient rather than too strict — the screen would let
   marginal names through, not wrongly condemn good ones.

4. **Book-test power varies by symbol.** `fresh_quote_coverage` ranges 0.04–0.90. Where
   coverage is low the crossed-share estimate rests on a smaller (and non-random)
   subsample — moments just after a BBO change. `mexc/AURASOL` is judged on 1,947 of
   46,774 prints. The proportion is still decisive at that n (3.3% crossed), but it is
   a selected sample and I flag it as such. `fresh_n` and `fresh_cov` are in the CSV for
   every symbol.

5. **What would falsify the headline finding.** If MEXC batches or re-timestamps deals
   server-side for low-activity contracts, the Uniform(6,16) spacing could be a
   reporting artifact rather than wash trading. That would **not** explain the ~$100k
   volume pinning, the coin-flip sides, or the 95%-inside-spread prints — three
   independent lines of evidence — but it is the alternative I could not rule out from
   our data alone. Confirming it would require order-book-level data we do not collect
   or a direct statement from the venue.

**Eight of thirty disqualified (27%)** is far below the >70% the NBER paper found across 29
unregulated venues. I read that as our universe already being pre-filtered by earlier
ёрш work, not as evidence the venues are clean.

---

## 8. Ranked shortlist

### SAFE to build a maker simulation on (CLEAN, 12)

| rank | symbol | worst day | latest | full | notes |
|---|---|---|---|---|---|
| 1 | gate/CHILLGUY_USDT | 95.9 | 95.9 | 97.9 | no weak component |
| 2 | gate/KOMA_USDT | 95.6 | 98.6 | 98.8 | |
| 3 | mexc/JIMOTHY_USDT | 92.1 | 97.3 | 98.3 | best MEXC name by a distance |
| 4 | gate/BMT_USDT | 90.7 | 90.7 | 100.0 | **ёрш locked-1-tick candidate — passes** |
| 5 | gate/OKB_USDT | 90.3 | 93.0 | 94.9 | |
| 6 | gate/MYX_USDT | 88.7 | 90.5 | 95.5 | **ёрш candidate — passes** |
| 7 | gate/FHE_USDT | 87.8 | 90.0 | 95.8 | |
| 8 | mexc/ANSEM_USDT | 84.4 | 89.1 | 94.8 | |
| 9 | gate/ONE_USDT | 81.3 | 88.7 | 92.3 | **ёрш candidate — passes** (contrast mexc/ONE below) |
| 10 | gate/KIOXIA_USDT | 80.8 | 94.3 | 92.0 | thin: 106/143 hours |
| 11 | gate/AAOI_USDT | 80.1 | 80.1 | 92.8 | |
| 12 | **gate/LA_USDT** | **74.8** | **92.9** | **93.7** | **ёрш best candidate — passes**; model the 530-lot (§6.2) |

### USABLE WITH CARE (SUSPECT, 8) — do not build on these without resolving the flag

| symbol | worst | veto | issue |
|---|---|---|---|
| gate/SKDD_USDT | 68.5 | size_conc | 46.1% at one size |
| gate/CRDO_USDT | 68.4 | size_conc | 58.6% at 0.01; thin (131/143 h) |
| mexc/LAB_USDT | 65.4 | sign_persist | alternation 44.5% |
| gate/BSP_USDT | 64.0 | — | score only; alternation 39.7% |
| mexc/CASHCAT_USDT | 57.3 | burstiness | CV 0.79 |
| mexc/ROBO_USDT | 53.9 | burstiness | CV 0.97; improving (crossed 44%→96%) |
| **mexc/ONE_USDT** | **48.8** | burstiness | **ёрш candidate — CV 0.48, alternation 46.7%, degrading 91%→32% crossed. Recommend dropping.** |
| mexc/BLUAI_USDT | 45.3 | sign_persist, burstiness | borderline; two vetoes |

### MUST BE DROPPED (DISQUALIFIED, 8)

| symbol | worst | vetoes | evidence |
|---|---|---|---|
| mexc/CATI_USDT | 41.7 | sign_persist, burstiness | alternation 53.2%, CV 0.60; improving lately but not enough |
| mexc/ENJ_USDT | 35.9 | book_cross, sign_persist, burstiness | **72.1% alternation** (anti-persistent); uniformity index 3.4 |
| mexc/WISHBONE_USDT | 26.4 | all four | $100,364/24h, obs/rep 1.006, 94.9% inside a 337 bps spread |
| mexc/KET_USDT | 25.9 | all four | $102,054/24h, obs/rep 1.001 |
| mexc/FRONG_USDT | 25.6 | all four | $101,537/24h, obs/rep 1.007 |
| **mexc/HOODRAT_USDT** | **25.6** | all four | known-toxic; caught only after the §6.1 rebuild |
| mexc/INDEX_USDT | 25.2 | all four | $103,387/24h, obs/rep 0.974, 88 distinct sizes |
| mexc/AURASOL_USDT | 22.8 | all four | $100,128/24h, obs/rep 0.988, **58 distinct sizes**, α 30.6 |


### INCONCLUSIVE (2) — do not clear, do not drop

`gate/SPCH_USDT` — **inconclusive, n = 999** (91/143 hours).
`gate/WEN_USDT` — **inconclusive, n = 2,302** (139/143 hours).

Neither reached 500 prints on any single day. Collect longer before judging.

---

## 9. What this means for the ёрш thesis

Of the five ёрш locked-1-tick candidates, **four pass and one does not**:

| candidate | verdict |
|---|---|
| gate/LA_USDT (best) | **CLEAN** 74.8 |
| gate/BMT_USDT | **CLEAN** 90.7 |
| gate/MYX_USDT | **CLEAN** 88.7 |
| gate/ONE_USDT | **CLEAN** 81.3 |
| **mexc/ONE_USDT** | **SUSPECT** 48.8 — recommend dropping |

The maker thesis survives this screen. The flow gate/LA prints against is organic:
sign-persistent, bursty, heavy-tailed, and it crosses the spread. **The premise that
there is real counterparty flow to collect from is not falsified for the Gate names.**

Two riders. First, `mexc/ONE_USDT` should come off the list — the same asset on Gate is
clean, so this is a venue artifact and not something a better simulator can price
around. Second, the queue-aware simulator should model gate/LA's 530-lot participant
explicitly rather than treating LA's flow as anonymous.

---

## 10. Reproduction

Read-only; nothing in this study wrote to the database or touched a service.

- Scripts: `stats_lib.py`, `analyze.py`, `book2.py`, `daily.py`, `score2.py` (session scratchpad)
- Pure-python statistics — no numpy/scipy is installed on `trading-server` and none was installed.
  χ² implementation validated against textbook critical values (§3.1).
- DB access via `psycopg2` with `set_session(readonly=True)`.
- Per-symbol output: `research/reports/data/tape_authenticity_by_symbol.csv` (30 rows, 63 columns),
  including all component scores, raw metrics, per-day crossed/spread series, and volume ratios.
