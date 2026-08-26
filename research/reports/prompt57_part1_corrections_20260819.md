# prompt-57 Part 1 — corrections on record before the synthesis

**Date:** 2026-08-19 · **Analyst:** Claude (session on `trading-server`)
**Scope:** §1a, §1b, §1c, plus a preliminary §3 *method*. **READ-ONLY.**
**Deliberately NOT run:** §2 (screen on the 42), §4 (portfolio), §5 (net-net stack).

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days) during a bear market — BTC topped
$126,199 in Oct 2025 and traded near $60,000 in mid-2026, a >50% drawdown. Thin-name liquidity
is depressed market-wide in this window. One regime, one sample.**

---

## 0. Why §2, §4 and §5 are absent

`mexc-carry-tape` started **2026-08-19T13:30:08Z** with `RuntimeMaxSec=3d`; it closes
**~2026-08-22T13:30Z**. At the time of writing it had run **4h53m**.

I had already run a preliminary §2/§4/§5 before the scope correction arrived. **That output has
been removed from the repository**, because it fails in a specific way that a caveat does not
neutralise: at 5 hours **every survivor has exactly one scoring window**, and with one window
there is no bad-window selection — which is the entire mechanism by which this screen detects
deterioration. **HOODRAT looked clean on any single early window; that is exactly the error that
produced the original 84.0 = CLEAN.** Verdicts of that kind propagating into a decision document
would have been worse than no document.

For the record, and so the work is not lost: the preliminary run produced 15 CLEAN / 13 SUSPECT
/ 11 NOT CLASSIFIED / 1 DISQUALIFIED / 5 PENDING, an 11-name INCLUDE list, and a net-net premium
of +34.8 to +38.3 pp over the ECB deposit facility. **None of those numbers should be used.**
They are retained outside the repo and will be regenerated properly after 2026-08-22.

---

## 1. §1a — The FRONG conclusion was a collection artifact. Retracted.

**Confirmed.** Every symbol in the ёрш tape set has its first print inside a **273.5-second
window**:

| | value |
|---|---|
| symbols in the ёрш tape set | 30 |
| earliest first print | 2026-08-13 13:35:40.805 UTC |
| latest first print | 2026-08-13 13:40:14.275 UTC |
| **spread, first to last** | **273.5 seconds** |

Thirty markets do not begin trading within 4½ minutes of one another. That is the collector
starting, and `mexc-ersh-tape` activated at 13:39:11 on the same date.

**Therefore: "FRONG's tape starts 10 days after it listed" carries no information about FRONG.**
The volume-quota report stated this as evidence *against* the listing-obligation hypothesis.
That was wrong. **The hypothesis is UNTESTED, not refuted.**

### What still stands — kept separate, as instructed

The spread observation comes from `funding_basis_snapshots`, which genuinely covers FRONG from
**2026-08-03 10:24:27** — 4,668 snapshots, from its first appearance in the universe. The perp
spread crossing ~250 bps on **2026-08-09** is real and unaffected.

It remains *suggestive* that the synthetic geometry post-dates listing by ~6 days, because
prints-inside-a-wide-spread requires a wide spread and there wasn't one before 08-09. But that
is now indirect evidence standing alone, not a refutation.

### Can the volume series test it? No — and that is worth stating

Reported volume has only been persisted since **2026-08-19T12:59:25Z**, with no backfill.
FRONG currently reads 103.6k–106.1k (perp) against 84.8k–95.8k (spot) — already a tight band,
but five hours cannot speak to 2026-08-03. **The listing-obligation question is unanswerable
from data we hold and will remain so; it needs a symbol that lists *after* today.**

### Unplanned finding from the new column: the $100k floor is venue-wide

Distribution of mean reported 24h **perp** volume across the live universe:

| | MEXC (n=665) | Gate (n=525) |
|---|---|---|
| minimum | **$71,423** | $1,301 |
| 1st percentile | **$101,134** | $2,748 |
| 5th percentile | $102,628 | $6,590 |
| median | $192,012 | $90,822 |
| symbols below $75k | **1 of 665** | 79 below $15k alone |

**MEXC has a hard floor at ~$100k; Gate does not.** 99% of MEXC perps report over $101k while
Gate's distribution decays naturally to $1,301. The floor is **perp-only** — the same MEXC
contracts show $53k–$67k *spot* volume against $100.6k perp.

**Caveat:** a floor is equally consistent with *fabrication* and with *delisting anything below
a threshold*. Tape evidence demonstrates fabrication for the five names examined; whether the
other ~88 in the $95–110k band are fabricated is untested. Either way, **MEXC reported volume
is unusable as a liquidity signal.**

---

## 2. §1b — LGD jackknife

| statistic | all 16 deaths | excluding gate/VANRY (n=15) | change |
|---|---|---|---|
| mean | **7.56%** | **3.63%** | **−52%** |
| median | 3.64% | 3.61% | −1% |
| **p95** | **66.52%** | **6.75%** | **−90%** |
| max | 66.52% | 6.75% | |

Full leave-one-out: the mean ranges **3.63% – 7.96%**. A single deletion moves it by up to
**4.33 pp**.

### Uncertainty on the 1.84 pp/yr drag, both sources propagated

| venue | PD annual [95% CI] | drag at mean LGD | drag, both CIs combined | drag jackknifed |
|---|---|---|---|---|
| **mexc** | 24.3% [13.4%, 38.6%] | **1.84 pp/yr** | **[0.00, 5.90] pp/yr** | **0.88 pp/yr** |
| **gate** | 11.3% [3.2%, 26.5%] | 0.85 pp/yr | [0.00, 4.05] pp/yr | 0.41 pp/yr |

LGD mean 7.56%, sd 15.81%, se 3.95%, **95% CI [0.00%, 15.30%]** — the interval includes zero
because n=16 and one observation is 8.8× the median.

**1.84 pp/yr is not a point estimate.** It is the centre of a range spanning an order of
magnitude, and the median-based figure (3.64%) and the jackknifed mean (3.63%) agree closely
with each other while disagreeing with the full mean — which is what a single dominant outlier
looks like. **Both the full and jackknifed figures must appear wherever the drag is quoted.**

---

## 3. §1c — Classification rule fixed, screen re-run on the 30 ёрш symbols

Three changes, as agreed:

1. **Rolling window replaces the 500-prints-per-calendar-day rule.**
2. **Uniformity index promoted to weight 10**, replacing benford (5) + price_disp (5).
3. **Worst-window scoring retained and explicitly labelled** the unvalidated post-hoc choice —
   the veto rule is not load-bearing (HOODRAT fails at every floor including 0), so the fitting
   lives here.

### An over-correction caught and reverted

A strict **500-print** rolling window produced **zero CLEAN verdicts across all 30 symbols**. A
500-print window is far shorter than a calendar day, so per-window statistics are noisier and
drag every composite down. A screen that classifies nothing is not a screen.

**The shipped rule keeps a window equal to a calendar day whenever that day holds ≥500 prints,
and extends forward only for thin symbols.** Dense names are unaffected; only the case §1c was
about is changed.

A second bias was fixed at the same time: the bad-window statistic is now the **10th percentile
of window scores, not the strict minimum**. A symbol with 343 windows otherwise gets 343 chances
to draw a bad one while a symbol with 2 gets two — the strict minimum is not comparable across
symbols with very different print volumes.

### Result

| | previous | **amended** |
|---|---|---|
| CLEAN | 12 | **9** |
| SUSPECT | 8 | **6** |
| DISQUALIFIED | 8 | **8** — identical set |
| NOT CLASSIFIED | — | **7** |
| INCONCLUSIVE | 2 | **0** |

Nine verdicts changed; the DISQUALIFIED set is unchanged, which is the stability one would want.

### Do SPCH and WEN become classifiable? Yes — both.

| symbol | before | **after** | windows |
|---|---|---|---|
| gate/SPCH_USDT | INCONCLUSIVE | **CLEAN (82.5)** | 5 |
| gate/WEN_USDT | INCONCLUSIVE | **SUSPECT (61.2)** | 6 |

Neither could ever have been classified under the old rule — SPCH's best calendar day was 404
prints and WEN's 476, both below a 500-print-per-day threshold. That was a defect in the
instrument and it is now fixed.

### Does gate/LA's ±25% instability change under the amended weighting? Yes — it resolves.

| | previous screen | **amended screen** |
|---|---|---|
| verdict | CLEAN | **CLEAN** |
| score | 74.8 (→ 69.6 under book_cross +25%) | **83.6** |
| ±25% sensitive? | **YES — NOT CLASSIFIED** | **NO** |

**gate/LA is now robustly CLEAN.** The reason is precisely the promoted component: LA's
uniformity index is **126–189**, unambiguously organic, so adding it at weight 10 lifts the
composite clear of the CLEAN/SUSPECT boundary that it previously sat 4.8 points above. This is
the out-of-sample-validated component doing what the hold-out predicted it would.

Remaining NOT CLASSIFIED under ±25% (7 of 30): gate/AAOI, gate/KIOXIA, gate/ONE, mexc/ANSEM,
mexc/BLUAI, mexc/LAB, mexc/ROBO. **Note that all three double-collection exclusions
(mexc/BLUAI, mexc/LAB, gate/ONE) land here** — scored from the ёрш tape rather than left in the
gap between collectors, and all three are threshold-sensitive.

---

## 4. §3 — Depth-to-size method (PRELIMINARY, 21 names only)

**Sized here: only the 21 survivors that already had `carry_book_l2` history before
2026-08-19T13:28:12Z** — 94–95 hours each, 317–997 paired snapshots. **The 24 names added at
13:28:12Z hold roughly five hours of snapshots and are deliberately NOT sized.** They will be
sized when §2 runs.

### Method, with assumptions printed

> Maximum position = **20% of the worst-decile (p10) depth on the thinner of the two legs.**
> Worst-decile rather than median because **exit is the binding side** — a carry position is
> unwound under worse conditions than it is opened.

### A discrepancy that had to be resolved first

The paper bot reports gate/WET depth as **$9,620**; a top-5 calculation gives **$93.8**. Both
are correct and measure different things:

| gate/WET_USDT | perp | spot |
|---|---|---|
| all-50-level p10 | **$914.8** | **$9,597.8** |
| all-50-level median | $1,201.7 | $26,295.6 |

**The bot's $9,620 matches the SPOT leg (9,597.8) almost exactly, while the PERP leg is 10×
thinner at $915.** A carry pair can only be sized on its thinner leg. This strongly indicates
the running paper bot is sizing on the wrong leg for at least this name — **reported, not
fixed, per instruction.** I have not traced the bot's full depth path, so this is an indication
requiring confirmation, not an established defect.

It also exposes a methodological point: **which leg binds is depth-dependent.** For gate/WET,
spot is thinner on a top-5 basis and perp is thinner on a 50-level basis.

### Output across the 21 (a method demonstration, NOT a book capacity)

| basis | total across 21 | median per name |
|---|---|---|
| **top-5** | **$584** | $17.40 |
| **full 50-level** | **$19,314** | $638 |
| ratio | **33.1×** | |

**The 33× gap between the two bases is the single most consequential unresolved number in this
project.** A carry position is entered patiently over hours, which argues for the 50-level
figure; a forced exit at a delisting happens against whatever is visible, which argues for
top-5. For context the earlier capacity study concluded **€20.8k**, close to the 50-level
figure.

**Neither number is a book capacity** — this is 21 of 45 names, and authenticity gating has not
been applied. It is here to show the method works and to surface the 33× question early.

---

## 5. What I could not determine, and why

1. **Anything requiring authenticity verdicts on the 42 survivors.** The tape window closes
   ~2026-08-22T13:30Z. §2, §4 and §5 are blocked until then, by design.
2. **Whether FRONG's flow was synthetic at listing** — and now, whether it *ever* can be
   determined. The tape record cannot reach 08-03 and volume persistence began today. This
   question is closed to us for FRONG specifically; it needs a symbol that lists after today.
3. **Whether the ~88 other MEXC names in the $95–110k band are fabricated.** The floor is
   established; fabrication is demonstrated only for the five with tape evidence. A floor is
   equally consistent with delisting-below-threshold.
4. **The true LGD distribution.** n=16 with the tail defined by n=1. The 95% CI on the mean
   includes zero. No amount of re-analysis fixes this; it needs more deaths, which means more
   time.
5. **Whether worst-window scoring generalises.** Still the load-bearing post-hoc choice, still
   unvalidated out-of-sample, and validating it needs a second labelled bad name that the
   averaged screen passes.
6. **Which depth basis is real (33×).** No amount of passive collection answers this — it needs
   an execution test measuring realised slippage against the quoted book. The project's own
   history is that modelled and realised costs diverged catastrophically once before
   (mark-price arbitrage: 95% modelled win rate, −214 bps realised).
7. **Worst-hour depth for the 24 new names.** They hold ~5 hours; worst-hour coverage needs a
   full day at minimum, and the earlier capacity study showed worst-hour is what actually binds.

---

## Deliverables

- `research/reports/data/depth_sizing_method_21names.csv` — 21 rows, both depth bases, hours of
  history and snapshot count per name so the reader can judge sufficiency.

## Next action

Re-run §2 after **2026-08-22T13:30Z**, then §3 for all 45, then §4 and §5. The amended screen
(rolling window + uniformity index at weight 10 + p10-of-windows) is the version to use.
