# Universe lifecycle audit — listings, delistings, token deaths

**Date:** 2026-08-19 · **Analyst:** Claude (read-only session on `trading-server`)
**Source:** `funding_basis_snapshots`, 7,862,340 rows, 2026-07-27 13:09 UTC → 2026-08-19 12:14 UTC
**Status:** READ-ONLY. No schema change, no writes, no service touched.

**Sample regime: collected during a bear market — BTC topped at $126,199 in Oct 2025 and
traded near $60,000 in mid-2026 (>50% drawdown). All findings describe this regime only.**

---

## 0. Read the caveats before the results

Four constraints bound everything below. They are stated first deliberately.

1. **The window is 23 days, not months.** `min(ts)` is 2026-07-27. Any annualised figure
   extrapolates a 23-day observation by ~16×. Treat annual death rates as order-of-magnitude.
2. **16 deaths is a small sample, and they are not independent** — they arrive in 10 batches,
   two of which contain 3 and 5 symbols. Confidence intervals that assume independence are
   too narrow; both versions are given in §3.1.
3. **T5 is underpowered.** With 16 deaths, no coverage of the announcement itself, and no
   confirmed announcement dates, the delisting-as-deadline question gets a directional
   answer, not a tradeable one.
4. **`perp_depth5_usd` and `spot_depth5_usd` are 100% NULL for all 7.86M rows.** Any part of
   this analysis that would have used depth uses spread instead. Reported, not fixed (§7).

### Row count vs window — no shortfall

The stale note of "~6.2M rows" is out of date; the table now holds **7,862,340**. Checking
for the implied-coverage gap the brief asks about:

- Expected: 22.96 days × 288 cycles/day × ~1,193 rows/cycle ≈ 7.89M
- Observed: 7.86M → **99.6%**. The row count is *consistent* with the full window.

There is no hidden coverage shortfall. That is a negative finding, and it is the reason
the lifecycle classification below can be trusted.

---

## 1. Schema and universe (as actually found, not as remembered)

`funding_basis_snapshots` columns: `id, ts, exchange, symbol, perp_mark, spot_price,
basis_bps, funding_rate, funding_interval_hours, mins_to_funding, funding_annualized_pct,
perp_bid, perp_ask, perp_spread_bps, spot_bid, spot_ask, spot_spread_bps,
perp_depth5_usd, spot_depth5_usd`.
Indexes: pkey on `id`, `idx_fbs_symbol (symbol, exchange)`, `idx_fbs_ts (ts DESC)`.

| | rows | distinct symbols | first ts | last ts |
|---|---|---|---|---|
| **gate** | 3,462,063 | 529 | 2026-07-27 13:09 | 2026-08-19 12:14 |
| **mexc** | 4,400,277 | 684 | 2026-07-27 13:09 | 2026-08-19 12:14 |
| **total** | 7,862,340 | 1,213 pairs | — | 22.96 days |

Two related tables exist and were checked: `carry_funding_intervals` (129 rows, the *real*
funding intervals) and `symbol_states` (an arb-era table, no `ts`, not relevant here).

**Data-quality finding — the funding-interval hardcode is visible in the data.**
`funding_interval_hours = 8` for **all 7,862,340 rows**, but `carry_funding_intervals`
records the truth: **95 of 129 symbols settle every 4h**, only 34 every 8h. Therefore
`funding_annualized_pct` in this table is understated ~2× for most symbols. **All funding
analysis below uses the raw `funding_rate`, never `funding_annualized_pct`.**

---

## 2. Coverage timeline — the gate for everything else

Cycles are bucketed to the 5-minute grid (`CARRY_CYCLE_SECONDS=300`). Median inter-cycle
gap is exactly 300.0 s.

| metric | value |
|---|---|
| cycle grid | 2026-07-27 13:05 → 2026-08-19 12:10 |
| cycles expected | 6,614 |
| cycles observed | 6,588 |
| **cycles missing** | **26 (0.39%) — uptime 99.61%** |
| median universe, gate | 525 symbols/cycle |
| median universe, mexc | 668 symbols/cycle |

### Health threshold

**`coverage_healthy = (symbols_in_cycle ≥ 0.80 × exchange median)`.** Chosen because the
observed distribution is strongly bimodal — normal cycles sit within ±1% of the median
(gate p01=525, p99=528; mexc p01=661, p99=672), while degenerate cycles sit at 2–7% of it.
Any threshold between 0.10 and 0.95 selects the same 4 cycles per exchange, so the result
is insensitive to the choice.

**Unhealthy cycles: 4 per exchange — all at 2026-07-27 13:05–13:20**, at 12–36 symbols
(2–7% of median). This is the collector's **ramp-up from the 12 hardcoded coins to the full
dynamic universe** (prompt-30), captured in the data. The dynamic universe is live from
**2026-07-27 13:25**, which is the `healthy_start` used for all classification.

### The 26 missing cycles

| resumed at | cycles lost | minutes |
|---|---|---|
| 2026-08-07 13:35 | 10 | 50 |
| 2026-07-31 10:15 | 4 | 20 |
| 2026-08-19 10:05 | 2 | 10 |
| 2026-07-29 23:45 · 2026-08-02 12:20 · 2026-08-03 14:05 · 2026-08-04 04:55 · 2026-08-05 01:00 · 2026-08-07 13:50 · 2026-08-11 20:35 · 2026-08-19 11:30 · 2026-08-19 11:40 · 2026-08-19 12:00 | 1 each | 5 each |

**Unreliable zone** = any cycle within ±2 cycles (±10 min) of a missing or unhealthy cycle:
80 cycles per exchange. Any first/last-seen falling inside it is classified UNRELIABLE.

### No collector step-changes after the ramp

Universe size is flat for the whole window: gate 525–528, mexc 661–672 (a slow net drift
upward on MEXC from new listings). **There is no second universe-definition change**, so no
appearance/disappearance in this window is a collector artifact of that kind. The one
structural move — gate 528 → 525 on 2026-07-31 — is three genuine symbol deaths (§3), not a
definition change.

---

## 3. Symbol lifecycle classification

Rules: NEW_LISTING if `first_seen` > 24 h after healthy start; DISAPPEARED if `last_seen` >
24 h before window end; INTERMITTENT if presence_ratio < 0.95 within its own span;
UNRELIABLE if the event cycle falls in the unreliable zone; else CONTINUOUS.

| classification | gate | mexc | total |
|---|---|---|---|
| CONTINUOUS | 524 | 653 | **1,177** |
| NEW_LISTING | 1 | 19 | **20** |
| DISAPPEARED | 4 | 12 | **16** |
| INTERMITTENT | 0 | 0 | **0** |
| UNRELIABLE | 0 | 0 | **0** |
| **total** | 529 | 684 | **1,213** |

**Every symbol has presence_ratio = 1.000.** Not one symbol flickers in and out within its
span. This is a strong data-quality result: the perp∩spot intersection is stable, so
appearance and disappearance are unambiguous, and no event needed the UNRELIABLE bucket.

**16 DISAPPEARED is a handful. T5 is underpowered, and that is a legitimate result.**

### Deaths arrive in batches on round hours

| last seen (UTC) | n | symbols |
|---|---|---|
| 2026-07-28 07:00 | 3 | mexc IQ, TCC, TJR |
| 2026-07-31 07:55–08:15 | 3 | gate VIC, WAVES, HIGH |
| 2026-08-06 13:00 | 1 | mexc ACX |
| 2026-08-07 08:00 | 1 | mexc VIC |
| **2026-08-10 07:00** | **5** | mexc ARROW, JUGGERNAUT, KINS, LEVI, VEXAI |
| 2026-08-10 14:00 | 1 | mexc GROVE |
| 2026-08-12 03:55 / 08:00 | 2 | gate VANRY, mexc VANRY |

Three independent facts argue these are genuine scheduled delistings rather than data
artifacts: they land on **round hours** (07:00, 08:00, 13:00, 14:00), they arrive in
**batches** (3 and 5 symbols simultaneously), and **VANRY died on both venues within 4h**
while **VIC died on both venues** (gate 07-31, mexc 08-07). A collector fault would not be
venue-correlated in that way.

**Classification is unconfirmed against exchange announcement history.** I did not fetch
MEXC/Gate delisting announcements — see §7.

---

## 4. T7 — token-death base rate and pre-death signature

### 4.1 Base rate

Exposure: 1,213 symbols × 22.96 days = **27,850 symbol-days**, 16 deaths.

| basis | events | hazard (per symbol-day) | **annualised death probability** | 95% CI |
|---|---|---|---|---|
| per-symbol (assumes independence) | 16 | 5.745 × 10⁻⁴ | **18.9%** | 11.3% – 28.9% |
| **per-batch (10 clusters — the honest one)** | 10 | 3.591 × 10⁻⁴ | **12.3%** | 6.1% – 21.4% |

Raw counts, not just percentages: **16 deaths / 1,213 symbols / 23 days**. Over the observed
window itself the per-symbol probability is only **1.31%**.

Because deaths cluster, the per-batch row is the defensible one: **roughly 12% of listed
thin perps disappear per year, 95% CI 6–21%.** By venue: mexc 12/684 → 24.3% annualised;
gate 4/529 → 11.3%. **MEXC delists roughly twice as aggressively as Gate.**

### 4.2 Pre-death signature — spread, normalised, vs matched controls

Each dead symbol is matched to its **5 nearest surviving symbols on the same exchange** by
median `perp_spread_bps` over days −14 to −7, tracked over the **same calendar window**
(this matters: a common-cause market move would otherwise masquerade as a death signal).
Each series is normalised to its own −168h..−120h baseline, so the comparison is a
*multiple*, not a level.

| hours before death | DEAD spread/baseline | CONTROL spread/baseline |
|---|---|---|
| −144h | 1.01× | 1.00× |
| −120h | 1.05× | 1.00× |
| −96h | 1.03× | 0.99× |
| −84h | 1.14× | 1.03× |
| **−72h** | **1.30×** | 0.95× |
| −60h | 1.32× | 1.03× |
| −48h | 1.34× | 0.99× |
| **−36h** | **2.20×** | 0.99× |
| −24h | 2.84× | 0.98× |
| −12h | 4.26× | 1.04× |
| −0h | 3.65× | 1.02× |

**The controls are flat at ~1.0× across the entire 7 days.** The dead names separate at
**−72h (1.30×)**, and become unmistakable at **−36h (2.20×)**. So there is an observable
pre-death signature, and it gives roughly **3 days of warning, with high confidence at 1.5 days.**

### 4.3 The critical caveat — the warning only exists for names that were liquid

Splitting the 16 deaths by baseline spread destroys the average:

| group | n | spread multiple (final ÷ −7d) | price change over final 7d |
|---|---|---|---|
| **LIQUID baseline (<20 bps)** | 4 | **median 25.30×** (3.93 – 92.59) | median −16.3% |
| **ILLIQUID baseline (≥20 bps)** | 6 | **median 0.91×** (0.75 – 4.40) | median −12.4% |
| no baseline (died <14d into window) | 6 | median 1.22× (0.88 – 2.00) | median −3.9% |

LIQUID group: mexc ACX, mexc VANRY, gate VANRY, mexc VIC — blowouts of 4×, 93×, 4×, 46×.
ILLIQUID group: mexc ARROW, GROVE, JUGGERNAUT, KINS, LEVI, VEXAI — **the spread does not
move, because these names were already at their terminal spread (120–312 bps) before they
died.** Their median multiple is *below 1.0*.

**This is the finding that matters for ёрш.** The spread-blowout alarm works on names that
had a tight market and lost it. It gives **no warning at all** on names that were already
wide — which is exactly the population a thin-name maker strategy trades. n=4 and n=6, so
this split is indicative, not established.

### 4.4 Deliverable — a rule the ёрш sim can consume

```
# Thin-name inventory / holding rule, derived from 16 deaths over 23 days (bear regime)

MAX_HOLDING_HOURS = 24
    # The reliable warning window is 72h (1.30x) with confirmation at 36h (2.20x).
    # A 24h cap keeps a full position turn inside the earliest warning, so no position
    # is ever carried through an unobserved onset.

KILL_SWITCH:
    exit if perp_spread_bps > 1.30 * trailing_7d_median_spread
           sustained for 2 consecutive hours
    # Fires ~72h before death on liquid names. Control group never reaches 1.30x
    # (max observed control value 1.06x), so the false-positive rate on survivors is low.

MAX_INVENTORY_PER_NAME:
    size so that a forced exit at 4x the normal half-spread costs <= 25 bps of capital:
        max_notional = 0.0025 * capital / (1.5 * baseline_spread_bps / 10000)
    # 4x is the median terminal spread multiple across ALL deaths (3.65x, rounded up).
    # The 1.5 factor = (4 - 1) x half-spread, the EXCESS cost over a normal exit.
    # Worked example, gate/LA (baseline ~10 bps), capital EUR 1,000:
    #     max_notional = 0.0025 * 1000 / (1.5 * 0.0010) = EUR 1,667

HARD OVERRIDE for names with baseline spread >= 20 bps:
    MAX_HOLDING_HOURS = 8   and   MAX_INVENTORY = half the formula above
    # These names gave ZERO spread warning before dying (median multiple 0.91x).
    # The only defence available is a shorter holding period and a smaller position.
```

The binding constraint is **not** the spread cost — at a 12% annual hazard and a 4× exit
spread, the expected annual death cost on a 10 bps name is only ~2–3 bps of position. The
binding constraint is the **basis decoupling in §5**, which a delta-neutral book cannot
hedge out.

---

## 5. T5 — behaviour into disappearance

All 16 deaths aligned on `last_seen` (event time 0), 6-hour buckets, medians.

### 5.1 Is it direction-neutral and mechanical, or just price collapse?

**The null (price collapse) is only partly supported, and it is not the whole story.**

Price change over the final 7 days, all 16 deaths:
`median −6.8%, q25 −19.0%, q75 −1.8%, min −47.5%, max +104.0%`

**Three of sixteen names went UP into delisting** — mexc JUGGERNAUT **+104.0%**,
mexc VEXAI +6.0%, mexc TCC +0.1%. A median of −6.8% is a mild drift, not a collapse. So
"these are just dying tokens falling to zero" is **rejected as a complete explanation**.

### 5.2 But the distortion is directional, not neutral

| hours before death | DEAD funding_rate | CONTROL funding_rate | DEAD \|basis\| bps | CONTROL \|basis\| bps |
|---|---|---|---|---|
| −144h | +0.000050 | +0.000050 | 33.2 | 18.6 |
| −96h | −0.000039 | +0.000050 | 40.4 | 26.7 |
| −66h | −0.000121 | +0.000100 | 26.2 | 17.3 |
| −48h | −0.000001 | +0.000050 | 57.5 | 21.7 |
| −24h | −0.000034 | +0.000100 | 28.5 | 20.5 |
| −12h | −0.000121 | +0.000084 | 49.3 | 12.8 |
| −0h | −0.000118 | +0.000050 | 57.0 | 16.1 |

Two consistent effects in the final ~3 days:

1. **Funding flips negative** on dying names (−0.00012) while controls stay positive
   (+0.00005 to +0.00010). Three names flip sign outright: ARROW, JUGGERNAUT, LEVI, all
   +0.0002 → ≈ −0.00012.
2. **Absolute basis runs 2–3.5× the control level** (57.0 vs 16.1 bps at t=0).

Negative funding plus negative basis is a **perp discount** — consistent with forced
long-side exit ahead of a known settlement, not with a mechanical symmetric convergence.

**So the answer to T5 is: the distortion is real and systematic, but it is directional
(perp trades at a discount), not direction-neutral.** It is the signature of one-sided
forced flow. That makes it far less attractive than expiry convergence: there is no
neutral spread to collect, only a directional bet on a name that is about to stop trading.

The extreme case shows why this is dangerous rather than profitable: **gate/VANRY reached a
final basis of +6,620 bps (66%)** — a perp/spot decoupling that would obliterate any
delta-neutral carry position long before the delisting settled.

### 5.3 Announcement cross-check — not done

See §7. The batch-on-round-hour structure is strong circumstantial evidence, but **the
classification is unconfirmed against published announcements.**

---

## 6. T4 — listing event study

20 NEW_LISTING events (19 MEXC, 1 Gate). MEXC lists far more aggressively than Gate.

### 6.1 The entry problem, stated honestly

`first_seen` is when the symbol entered *our universe scan*, which lags the true exchange
listing by up to one 5-minute cycle. Worse: **19 of 20 new listings had NULL
`perp_spread_bps` at first sighting** — there was no quotable two-sided market yet.

The first quotable moment arrives **median 21.1 minutes** after first sighting
(range 2.3 – 51.9 min), at a **median perp spread of 246 bps** (range 10.3 – 741.9).

All returns below are therefore measured **from the first quotable price**, not from first
sighting. Anything measured from first sighting would be untradeable fiction.

### 6.2 Gross return distribution (full distribution, per the brief)

| horizon | n | min | q25 | median | q75 | max | mean |
|---|---|---|---|---|---|---|---|
| +5m | 20 | −19.76 | −1.58 | 1.72 | 4.09 | 100.00 | 5.17 |
| +1h | 20 | −32.45 | −8.01 | −3.27 | 4.52 | 45.94 | −0.40 |
| +6h | 20 | −42.96 | −11.87 | −0.06 | 15.74 | 93.28 | 3.11 |
| **+24h** | 20 | −56.14 | −32.13 | **−19.06** | 5.19 | 42.13 | −13.33 |

The +5m max of +100.00% is **mexc/HMM_USDT and it is a bad tick** — `perp_mark` doubles for
exactly one cycle (0.01868 → 0.03736 → 0.02517) then reverts. Excluding it, +5m becomes
`min −19.76, q25 −1.64, median 1.44, q75 3.23, max 8.01, mean 0.18`.

**New listings drift down hard: median −19.06% at 24h, with 14 of 20 negative.**

### 6.3 Net of real costs — the only question that matters

Cost model: cross the entry spread once + perp taker fee both sides (MEXC 0.05%, Gate 0.05%).
**Median round-trip cost: 2.56%.** Maker fees are not used because you cannot rely on a
passive fill in the first minutes of a listing.

| horizon | median net (long) | profitable | median net with **perfect direction foresight** | profitable |
|---|---|---|---|---|
| +5m | −1.32% | 6/20 | −0.59% | 9/20 |
| +1h | −5.68% | 7/20 | +4.12% | 17/20 |
| +6h | −1.84% | 9/20 | +9.64% | 17/20 |
| +24h | −21.31% | 6/20 | +23.58% | 20/20 |

**Verdict: no capturable edge.**

- Naive long is **loss-making at every horizon** (median −1.3% to −21.3%).
- Even with **perfect foresight of direction** — an upper bound nobody achieves — the
  **+5m horizon is still negative** (median −0.59%, 9/20). The move inside the first five
  minutes does not cover its own spread.
- The +24h perfect-foresight number (+23.58%, 20/20) only says these names are volatile.
  It is not an edge; it is the value of information this study does not provide.

Short-side capture is not assessed: borrow/short availability on a fresh MEXC listing is
not in this dataset.

---

## 7. What I could not determine, and why

1. **Whether the 16 disappearances were announced delistings.** I did not query MEXC or Gate
   announcement APIs, so the delisting classification rests entirely on the *shape* of the
   data (round-hour timing, batching, cross-venue correlation for VANRY and VIC). That
   evidence is strong but circumstantial. **The classification is unconfirmed.** A symbol
   removed because it fell out of the perp∩spot *intersection* — e.g. its spot pair was
   delisted while the perp survived — would look identical here. **This is the single
   largest unresolved confound in the report.**

2. **Whether the pre-death spread signature generalises.** It rests on 4 liquid deaths and
   6 illiquid ones. The direction of the split is consistent and mechanically sensible, but
   n=4 cannot establish a 25× median blowout. The rule in §4.4 is sized conservatively
   because of this.

3. **Anything requiring depth.** `perp_depth5_usd` and `spot_depth5_usd` are **NULL in all
   7,862,340 rows**. The inventory rule in §4.4 is therefore expressed in spread terms,
   which is a weaker proxy for the real constraint — how much size the book absorbs.
   Reported, not fixed, per instruction.

4. **True listing time, and therefore the first minutes of a listing.** Our clock starts at
   a 5-minute universe scan, and the first quotable spread is a further ~21 minutes out.
   Whatever happens in the first ~26 minutes of a MEXC listing is invisible to this dataset,
   and it is plausibly where any real listing edge lives.

5. **Whether the funding sign-flip is causal or compositional.** Funding turns negative into
   death, but dying names also drift down in price. I could not separate "forced-exit
   pressure creates a perp discount" from "falling price mechanically produces negative
   funding". Distinguishing them needs matched controls conditioned on *contemporaneous
   return*, not just on baseline spread — a stricter match than the 23-day window supports.

6. **Regime dependence.** Everything is one 23-day bear-market window. Delisting activity is
   plausibly counter-cyclical (exchanges purge dead names after drawdowns), so **12%/yr may
   be a cyclical high, not a through-cycle rate.** There is no second regime in this table
   to test against.

7. **`funding_annualized_pct` is unusable** (8h hardcode vs 95/129 symbols actually settling
   4h). I worked around it with raw `funding_rate`, but any historical analysis that has
   already consumed that column is wrong by ~2×.

---

## 8. Notes — things observed but deliberately not fixed

- `funding_interval_hours` is hardcoded to 8 in the collector; `carry_funding_intervals`
  already holds the correct per-symbol values. Not touched.
- `perp_depth5_usd` / `spot_depth5_usd` written as NULL for the whole table.
- `mexc/HMM_USDT` at 2026-08-12 06:32 has a one-cycle `perp_mark` doubling (bad tick).
- MEXC carries ~1.05% NULL `perp_bid`/`perp_spread_bps`, concentrated on fresh listings.
- `mexc/FRONG_USDT` appears here as a NEW_LISTING (2026-08-03) and was independently
  classified DISQUALIFIED as manufactured tape in `tape_authenticity_20260819.md`. A
  symbol being newly listed and wash-traded is not a coincidence worth ignoring.

## 9. Reproduction

Read-only; `psycopg2` with `set_session(readonly=True)`. Pure-python statistics (no numpy
on this host, none installed). Scripts: `lifecycle.py`, `deaths.py`, `listings2.py`
(session scratchpad). Full-table aggregates were single sequential scans; NULL-rate profiling
used deterministic 1-in-100 and 1-in-50 cycle sampling (`(epoch/300) % N = 0`), stated at
point of use rather than silently truncated.

Data: `research/reports/data/symbol_lifecycle.csv` (1,213 rows),
`deaths_pre_signature.csv` (1,737 rows), `listings_event_study.csv` (20 rows).
