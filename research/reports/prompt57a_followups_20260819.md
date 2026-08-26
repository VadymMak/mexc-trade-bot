# prompt-57a — the MEXC floor decomposed, the bot's depth path traced, a prospective test opened

**Date:** 2026-08-19 · **Analyst:** Claude (session on `trading-server`)
**Items 1–2 READ-ONLY. Item 3 adds a query file and a `CURRENT_STATUS.md` note — no service change.**

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days) during a bear market — BTC topped
$126,199 in Oct 2025 and traded near $60,000 in mid-2026, a >50% drawdown. Thin-name liquidity
is depressed market-wide in this window. One regime, one sample.**

---

## Headlines

1. **The floor is a target, not survivorship — and not a universe-wide additive component.**
   57 MEXC symbols sit in the single $100–105k bin after **19 consecutive empty bins below it**.
   Calibrated on Gate's natural shape, pure truncation predicts ~7.6. That is a **7.5× excess
   at a round number.**
2. **No authenticity verdict changes**, because reported volume was never a scored component of
   the screen. But **the ёрш universe-selection rule is void on MEXC** — and that is why 5 of 5
   MEXC "thinnest tier" names are DISQUALIFIED and 0 of 5 Gate ones are.
3. **My Part 1 flag on the paper bot was WRONG on sizing.** The bot sizes with a four-leg
   worst-hour round-trip slippage model. It does not size on one leg. **The narrower issue is
   real:** the R5 depth-collapse *monitor* is hardcoded to a single leg, and on 1 of 5 positions
   it watches a leg **8.8× thicker** than the binding exit leg.
4. **The new-listing watch is live** and correctly returns zero rows so far.

---

## Item 1 — Decomposing the MEXC volume floor

### 1.1 Is the floor universal, or confined to a subset?

The user's hypothesis: if the floor is universal, busy names show *floor + organic* and the five
pinned names are *floor only* — which would make them unremarkable rather than special.

**First test — flatness of observed hourly tape volume** (min ÷ median hourly, 5 full days):

| group | names | min/median hourly |
|---|---|---|
| the 5 pinned + HOODRAT, KET | mexc AURASOL, FRONG, INDEX, WISHBONE, KET, HOODRAT | **0.69 – 0.84** |
| other MEXC with tape | mexc CATI 0.50, BLUAI 0.50, ENJ 0.64, JIMOTHY 0.39, CASHCAT 0.27, ONE 0.26, LAB 0.18, ANSEM 0.14, ROBO 0.11 | 0.11 – 0.64 |
| Gate | BMT 0.13, KOMA 0.17, SPCH 0.15, WEN 0.03, AAOI 0.21, LA 0.30 | 0.03 – 0.30 |

The flat signature is confined to the disqualified/suspect names. **But this test cannot settle
the question**: a $4,167/hour floor component is invisible underneath $80,000/hour of organic
flow. Absence of flatness in busy names is not evidence of absence of a floor.

### 1.2 The test that does settle it — distribution shape at the boundary

Fabrication (values pushed *to* a target) produces a **pile-up at the target**. Truncation
(values below a threshold *removed*) produces a smooth distribution starting at the threshold.

Mean reported 24h perp volume, $5,000 bins:

| bin | MEXC | Gate |
|---|---|---|
| $0 – $70,000 (14 bins) | **0** | 231 |
| $70k – $95k (5 bins) | **1** | 28 |
| $95,000 – $100,000 | **1** | 2 |
| **$100,000 – $105,000** | **57** | 6 |
| $105,000 – $110,000 | 34 | 6 |
| $110,000 – $115,000 | 22 | 3 |
| $115,000 – $120,000 | 22 | 4 |

**Nineteen consecutive empty bins, then 57 symbols in one $5k bin.**

Calibrating on Gate's natural density (6 symbols in $100–105k out of n=525) and scaling to
MEXC's n=665 gives **~7.6 expected under pure truncation. Observed: 57. Excess: 7.5×.**

Truncation removes what is below a threshold; it does not *create* a spike above it. Under pure
survivorship the density just above the cut should be whatever the natural density is there —
Gate says that is small and smooth. **A 7.5× pile-up in the first $5k bin above a round number
is what a target looks like, not what a filter looks like.**

**Verdict: the floor is confined to a subset — the ~91 MEXC symbols in the $100–110k band — and
the mechanism is active volume maintenance to a $100k target, not a universe-wide additive
component and not pure delisting-below-threshold.**

**Not over-claiming:** this rules out *pure* truncation. It does not rule out a mixture — MEXC
may both maintain a floor *and* delist persistent under-performers. And of the ~91 in the band,
only 5 have tape evidence; the other ~86 are untested. What is established for all 91 is that
their reported volume sits at a round target with implausible density.

**The survivorship reading remains live in one respect worth carrying forward:** if MEXC does
delist perps that fall below a volume threshold, that is itself a death predictor, and it would
apply to exactly the thin names where the T7 spread signal gives no warning (already-wide names
showed median 0.91× spread change into death — no warning at all). **This is now testable
prospectively:** the volume column is live, so a MEXC perp drifting toward $100k from above can
be watched. It could not be tested retrospectively — the 12 MEXC deaths all pre-date volume
persistence.

### 1.3 Does this change any authenticity verdict? No.

The screen's scored components are `book_cross (25), size_conc (20), sign_persist (20),
burstiness (15), size_tail (10), uniformity (10)`. **Reported volume is not among them and never
was.** It appeared in `volume_quota_analysis_20260819.md` as corroborating narrative evidence,
not as a scored input.

So the residual-above-floor statistic, while it is the correct one to use in future, **changes
no verdict**. The tape-derived evidence (Uniform(6,16)s arrivals, coin-flip sides, 95% of prints
strictly inside a 230–380 bps spread) is independent of reported volume entirely.

**Recommendation for future use:** where reported MEXC volume is used at all, use
`max(0, reported − 100,000)` as the organic estimate, and treat any MEXC name whose reported
volume is inside $95–110k as having **unknown, possibly zero** organic volume.

### 1.4 What is now void — repo grep

| consumer | status |
|---|---|
| **`researcher/app/ersh/symbols.py:4`** — ёрш candidate selection, "24h quote volume $50k–$3M, MEXC `amount24`" | **VOID on MEXC** — see below |
| `researcher/app/tools/symbol_discovery.py:133` — MEXC spot `quoteVolume` | **unaffected** — spot has no floor ($53–67k observed on floor names) |
| `researcher/app/core/symbol_watcher.py:88` — MEXC spot `quoteVolume` | **unaffected**, same reason |
| `symbol_discovery.py:89-90`, `symbol_watcher.py:162` — Gate `volume_24h_settle/quote` | **unaffected** — Gate has no floor |
| `ml_trade_outcomes.quote_volume_24h_entry` | arb-era, frozen 1,888-row archive; moot |
| `funding_basis_snapshots.perp_volume24_usd` | the field itself; correct as *reported*, must not be read as *liquidity* |

### The consequence that matters: the ёрш selection rule was void on MEXC

The selection rule bands candidates by 24h quote volume and takes the thinnest tier first.
**MEXC has no perps below ~$100k**, so on MEXC the "thinnest tier" *is* the floor population:

| MEXC "tier $50k–$300k" | volume at selection | verdict |
|---|---|---|
| FRONG_USDT | 181,587 | **DISQUALIFIED** |
| KET_USDT | 103,118 | **DISQUALIFIED** |
| AURASOL_USDT | 102,567 | **DISQUALIFIED** |
| WISHBONE_USDT | 110,994 | **DISQUALIFIED** |
| INDEX_USDT | 103,951 | **DISQUALIFIED** |

**5 of 5.** The Gate equivalent tier (WEN 269,894 · SPCH 54,050 · SKDD 92,690 · CRDO 108,291 ·
KIOXIA 299,721) contains genuinely thin names — SPCH at $54k is *below MEXC's floor entirely* —
and **0 of 5 are disqualified**.

**This is the causal explanation for the whole authenticity finding.** The ёрш universe was not
a random sample that happened to contain fabricated names; the selection rule, applied to a
venue with a volume floor, *targets* the fabricated population by construction. Any future
selection on MEXC reported volume will do the same. Reported, not fixed.

---

## Item 2 — The paper bot's depth path: my Part 1 flag was wrong

**Correction: the bot does NOT size on a single leg.** Tracing it end to end:

| function | what it does | legs used |
|---|---|---|
| `Curve.capacity(t_bps)` | max USD absorbable with VWAP slippage-from-touch ≤ t_bps, one book side of one snapshot | one side |
| `round_trip_slip()` | sums worst-hour slippage over `LEGS` | **all four**: spot-ask (entry buy), perp-bid (entry short), spot-bid (exit sell), perp-ask (exit cover) |
| **`max_prudent_notional()` — R6, THE SIZE CAP** | binary-searches the notional keeping four-leg worst-hour round-trip slippage under `max_rt_slip_bps` | **all four** |
| `worst_hour_capacity()` — R5 reference | thinnest hour-of-day capacity, stored as `entry_depth_usd` | **`("spot","bid")` only, hardcoded** |

**The sizing model is sound and is more careful than the min-of-legs heuristic I compared it
against** — it is slippage-limited, worst-hour, and four-legged.

### The narrower issue is real: R5 monitors one hardcoded leg

`selector.py:144` and `risk.py:131` both call `leg_curves(ex, sym, "spot", "bid")`. So the
depth-collapse monitor — and the `too_big = now_depth < notional_usd` guard — watch the
spot-sell leg only.

Per-side worst-decile capacity for the five open paper positions (exit legs are spot-bid and
perp-ask):

| position | notional | R5 watches (spot bid) | perp ask | **binding exit leg** | ratio watched÷binding |
|---|---|---|---|---|---|
| gate/BTR | $58.4 | $876 | $2,996 | spot $876 | **1.0×** ✓ |
| gate/HANA | $119.9 | $957 | $3,848 | spot $957 | **1.0×** ✓ |
| gate/IDOL | $73.5 | $120 | $967 | spot $120 | **1.0×** ✓ |
| **gate/WET** | **$180.2** | **$9,620** | **$1,094** | **perp $1,094** | **8.8×** ✗ |
| mexc/H | $288.0 | $3,589 | $1,091,365 | spot $3,589 | **1.0×** ✓ |

**Worst case 8.8× (gate/WET). Median 1.0× — the monitor happens to watch the binding leg on 4 of
5.** `entry_depth_usd` for WET is 9,620.2 against a measured spot-bid p10 of 9,620 — an exact
match, which is what surfaced this.

### Are any conclusions from the paper bot affected?

**No.** Sizing is R6 (four-leg) and is unaffected, so **the bot's P&L, funding accrual and
realised-vs-modelled comparison stand.** What is overstated is the *sensitivity of the R5 alarm*
on names where the perp leg is thinner: for WET the reported safety margin is 8.8× larger than
the binding leg supports. At the current $180 notional there is no live breach — the binding
perp capacity is $1,094 — but the alarm would not fire on a perp-side collapse.

Five of the bot's six paper positions are in the carry survivor list; none of their *sizes* are
affected. **Reported, not fixed.**

---

## Item 3 — New-listing watch, opened

**`research/queries/new_listing_watch.sql`** — a read-only query, not a service.

Selects symbols whose `first_seen` is after **2026-08-19T12:59:25Z** (the volume-persistence
boundary) and reports, per new listing:

- `pinned_at_floor` — mean reported 24h volume inside the $95k–$110k band
- `hourly_flatness` — max ÷ min hourly volume (a constant-rate emitter ≈ 1; organic flow ran
  2.4–6.9% of day per hour, i.e. ≈ 2.9)
- `mean_first_24h_usd` / `hours_in_first_24h` — whether the signature is present **from day one**
  or appears later

**Result now: 0 rows**, as expected 5.6 hours past the boundary. MEXC listed ~19 perps in the
preceding 23 days (~0.83/day), so a first observation should arrive within days.

Recorded in `CURRENT_STATUS.md` under **STANDING WATCH**, explicitly stating that the hypothesis
is **UNTESTED, not refuted**, so a future session does not rediscover the artifact and repeat
the earlier error.

---

## Methodological note — which conclusions rest on what

**The DISQUALIFIED set is invariant across every aggregation rule tried.** Verified, not asserted:

| aggregation rule | DISQUALIFIED |
|---|---|
| 1. mean → worst **calendar day** (original) | 8 |
| 2. day-window, **p10 of windows** (shipped) | 8 — **identical set** |
| 3. strict 500-print window, p10 (**reverted**) | 9 |

**Invariant core, present under all three: AURASOL, CATI, ENJ, FRONG, HOODRAT, INDEX, KET,
WISHBONE.** Only `mexc/ROBO_USDT` is added, and only by the rule that was reverted for producing
zero CLEAN verdicts.

The aggregation rule has now been changed twice after seeing results. That is a real
multiple-comparisons hazard, and the invariance is the answer to it:

- **Rests on the invariant set — quote with confidence:** the identification of the eight
  manufactured names; the $100k quota finding; the volume-floor decomposition above; the
  conclusion that the ёрш selection rule targets the fabricated population.
- **Rests on a single rule — quote with the rule named:** every CLEAN and SUSPECT verdict, the
  NOT CLASSIFIED list, and **`gate/LA`'s score of 83.6**. LA moved 74.8 → 83.6 *because* the
  uniformity index was promoted — the one component validated out-of-sample first, behaving
  exactly as the hold-out predicted. That is the best-supported single-rule result in the set,
  and it is still a single-rule result.
- **Rests on a post-hoc choice that remains unvalidated:** worst-window scoring itself. The veto
  rule is not load-bearing (HOODRAT fails at every floor including 0), so the fitting lives here.

---

## What I could not determine, and why

1. **Whether the ~86 other MEXC names in the $100–110k band are fabricated.** The pile-up is
   established for all 91; tape evidence exists for 5. The rest are untested and will stay so
   unless tape is extended to them.
2. **Whether MEXC also delists below a threshold.** Ruled out as the *sole* explanation, not as
   a co-existing one. Untestable retrospectively — all 12 MEXC deaths pre-date volume
   persistence — but now testable going forward.
3. **Whether the R5 single-leg monitor has ever mattered.** No position has breached; the 8.8×
   gap on WET is a reduced margin, not a live failure. Whether it would have missed a real
   perp-side collapse cannot be known from a window in which none occurred.
4. **The listing-obligation hypothesis.** Still untested. The watch is open; it needs a listing.
5. **Whether the floor is $100k exactly or a band.** The cliff sits between $95k and $100k with
   one symbol in between, so the target is *approximately* $100k. A finer estimate needs more
   symbols near the boundary than exist.
6. **Everything remains one regime**, 23 days, bear market.

## Deliverables

- `research/queries/new_listing_watch.sql` — standing read-only test
- `CURRENT_STATUS.md` — STANDING WATCH note recording that the hypothesis is untested
