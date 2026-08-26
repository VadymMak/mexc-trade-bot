# Funding-interval correction and carry re-analysis (Phase 1)

**Date:** 2026-08-19 · **Analyst:** Claude (read-only session on `trading-server`)
**Source:** `funding_basis_snapshots` 7,867,128 rows · `carry_funding_intervals` 129 rows · live venue APIs
**Status:** Phase 1 complete, **READ-ONLY**. No writes, no schema change, no service restarted.

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days) during a bear market — BTC
topped $126,199 in Oct 2025 and traded near $60,000 in mid-2026. One regime only.**

---

## Verdict up front

1. **The bug is real and bigger than reported.** Not 74% of symbols — **66.4% (795 of 1,197)**
   settle faster than 8h and had their annualised carry understated, almost all by exactly **2×**.
2. **The parked verdict on majors SURVIVES, exactly.** BTC, ETH, SOL, XRP, BNB all settle at
   8h; their corrected APR moves by **0.00 percentage points**. mexc/BTC is 6.12% — squarely
   inside the "~6–7%" that was recorded. That is the control, and it passes.
3. **The verdict on the thin tail does NOT survive.** After correction, and after subtracting
   both-leg spreads and filtering for basis sanity and funding stability, the investable set
   goes from **12 names to 45 names**, with the top at 136% net APR. "Carry is a ~5–10%
   passive option" is true of majors and **false of the corrected tail**.
4. **No trading decision was made on bad arithmetic.** The live carry bot already fetches true
   intervals (`app/carry/bot/intervals.py` exists precisely because this column is broken).
   The contamination is confined to the stored column and to offline analysis built on it.
5. **Phase 2 is therefore warranted** — proposed in §7, not executed.

---

## 1. Ground truth on funding intervals

### 1.1 What `carry_funding_intervals` actually is

| property | finding |
|---|---|
| rows | **129** |
| written | **one burst, 2026-08-19 04:20:56 → 04:24:07** (a single ~3-minute pass) |
| history | **none** — one row per symbol, no versioning, no time series |
| sources | `gate.funding_interval` (68), `mexc.collectCycle` (61) |
| coverage of the 1,213-symbol universe | **10.6%** (gate 68/529 = 12.9%, mexc 61/684 = 8.9%) |

It is the carry bot's startup cache for its own 129-name candidate list, not a universe map.
**The "95 of 129 settle at 4h" figure in the prompt-51 report is a statement about that
biased 129-name subset**, not the universe. The true universe figure is 66.4% (§1.3).

Confidence in it is nonetheless **high**: validated against a live re-fetch below, 129/129 agree.

### 1.2 Better ground truth, and how it was validated

Two live bulk endpoints cover the universe:

- Gate `GET /api/v4/futures/usdt/contracts` → `funding_interval` (seconds) — **938 contracts**
- MEXC `GET /api/v1/contract/funding_rate` → `collectCycle` (hours) — **1,129 contracts**

(MEXC's `contract/detail`, used for contract multipliers, carries **no** funding-interval
field — I checked every key. The bulk `funding_rate` endpoint is the one that has it.)

**Validation 1 — against the stored table.** 129 stored vs live re-fetch:
**129 agree, 0 disagree, 0 missing.** The stored values are correct.

**Validation 2 — against our own data, independently.** A funding settlement leaves a
discontinuity in `funding_rate`. On Gate that reset is visible: gate/BTC_USDT's maximum
absolute change per UTC hour spikes to 1.5–1.9 × 10⁻⁴ at hours **0, 8, 16** against a ~1 × 10⁻⁵
baseline elsewhere — a 10–20× spike exactly on the 8h boundaries. Classifying every Gate
symbol by which hours carry ≥5× the median jump:

| | agree | disagree | unclassifiable |
|---|---|---|---|
| empirical vs live API (gate) | **377** | 8 | 143 (rate too flat to classify) |

**97.9% agreement between an exchange-independent inference from our own tape and the venue
API.** This is the strongest available evidence that the interval map is right.

*(The same method does not work on MEXC: mexc/BTC's hourly max-jump is flat at ~4 × 10⁻⁶
across all 24 hours — MEXC's ticker `funding_rate` drifts continuously with no visible reset.
MEXC therefore rests on the API alone, cross-checked by the 129-row agreement above.)*

### 1.3 Distribution across the universe

| interval | gate | mexc | corrected total |
|---|---|---|---|
| 4h | 344 | 563 | **793 (66.2%)** |
| 8h | 592 | 559 | **402 (33.6%)** |
| 1h | 2 | 6 | **2 in our universe** |
| 24h | — | 1 | 0 in our universe |

Of 1,197 correctable symbols: **795 need correction (664 at 2×, 2 at 8×), 402 are unchanged.**

### 1.4 Is the interval stable per symbol? No — quantified

**A single static mapping is an approximation, and I can measure the error.** The 8
empirical-vs-API disagreements are not noise; they are symbols whose interval *changed*:

| symbol | live API today | settled at, during our window | reading |
|---|---|---|---|
| gate 2Z, AEVO, MELANIA, OGN, ROBO, STO, ZIL | 4h | 8h boundaries only | **moved 8h → 4h at/after window end** |
| gate ESP | 8h | 4h boundaries | **moved 4h → 8h** |

**8 of 386 classifiable Gate symbols (2.1%) changed interval in or around a 23-day window.**
Applying today's map retroactively is therefore ~98% accurate over this window and will decay
with age. All 8 are flagged in the CSV (`interval_changed_in_window`).

### 1.5 Symbols that cannot be corrected — excluded, never imputed

**16 symbols** in `funding_basis_snapshots` have no interval available from either venue API:

`mexc` ACX, ARROW, GROVE, IQ, JUGGERNAUT, KINS, LEVI, TCC, TJR, VANRY, VEXAI, VIC ·
`gate` HIGH, VANRY, VIC, WAVES

These are **exactly the 16 DISAPPEARED symbols** identified in
`universe_lifecycle_20260819.md` — they are delisted, so the venues no longer publish them.
Listed in `data/excluded_symbols.csv`, excluded from every figure below. **No interval was guessed.**

---

## 2. Recomputation

Corrected APR uses raw `funding_rate` (never the stored `funding_annualized_pct`):

```
new_apr_pct = mean(funding_rate) × (24 / true_interval_h) × 365 × 100
old_apr_pct = mean(funding_rate) × (24 / 8)               × 365 × 100
```

**Formula verification:** the reconstructed `old_apr_pct` matches the stored
`funding_annualized_pct` with `max |difference| = 0.000000` across all 1,197 symbols. That
confirms the collector's formula is exactly the 8h hardcode and nothing else is going on.

### Cost model (unchanged from the old comparison, so the columns are comparable)

Round-trip cost = **median perp spread + median spot spread** (cross one full spread on each
leg over an entry-plus-exit round trip). `net_apr_1y = gross_apr − cost_bps/100`, i.e. a
one-year hold. `breakeven_days` = days of carry needed to repay the round trip.

**Both old and new columns carry the identical cost subtraction**, so every comparison below
isolates the interval correction and nothing else.

---

## 3. The control — majors must not move, and they don't

| symbol | true interval | old APR | new APR | **delta** | cost bps | new net |
|---|---|---|---|---|---|---|
| gate/BTC_USDT | 8h | 4.48% | 4.48% | **0.00** | 0.0 | 4.48% |
| **mexc/BTC_USDT** | 8h | **6.12%** | **6.12%** | **0.00** | 0.0 | **6.12%** |
| gate/ETH_USDT | 8h | 3.66% | 3.66% | **0.00** | 0.1 | 3.65% |
| mexc/ETH_USDT | 8h | 4.15% | 4.15% | **0.00** | 0.1 | 4.14% |
| gate/SOL_USDT | 8h | −0.40% | −0.40% | **0.00** | 2.7 | −0.42% |
| mexc/SOL_USDT | 8h | 3.36% | 3.36% | **0.00** | 2.7 | 3.33% |
| gate/XRP_USDT | 8h | 3.91% | 3.91% | **0.00** | 1.9 | 3.89% |
| mexc/BNB_USDT | 8h | 4.51% | 4.51% | **0.00** | 2.2 | 4.49% |

**The correction moves exactly what it should and nothing else.** mexc/BTC at 6.12% sits
inside the "BTC ~6–7%" already on record — that number was never wrong, because BTC was
always an 8h contract.

---

## 4. What changed — old vs new, side by side

All figures below are **net of both-leg spread** and restricted to symbols with ≥2,000 cycles
and **|mean basis| ≤ 200 bps** (see §4.3 for why that filter is mandatory).

### 4.1 Headline table

| # | symbol | iv | OLD net | **NEW net** | × | cost bps | % cycles + | basis bps |
|---|---|---|---|---|---|---|---|---|
| 1 | mexc/BTW_USDT | 4h | 67.78% | **135.81%** | 2 | 24.2 | 100.0 | 21.3 |
| 2 | gate/龙虾_USDT | 4h | 63.66% | **127.49%** | 2 | 17.1 | 93.8 | 23.0 |
| 3 | mexc/BROCCOLIF3B_USDT | 4h | 43.03% | **87.08%** | 2 | 101.6 | 94.1 | 30.2 |
| 4 | mexc/STAR_USDT | 4h | 40.53% | **81.77%** | 2 | 71.5 | 94.5 | 7.9 |
| 5 | mexc/CLO_USDT | 4h | 35.53% | **71.64%** | 2 | 56.8 | 100.0 | 6.0 |
| 6 | gate/AI_USDT | 4h | 35.53% | **71.52%** | 2 | 46.4 | 100.0 | 64.0 |
| 7 | mexc/BLUAI_USDT | 4h | 35.34% | **71.02%** | 2 | 33.2 | 99.4 | 47.5 |
| 8 | **gate/WET_USDT** | **8h** | 67.27% | **67.27%** | **1** | 23.1 | 100.0 | 27.1 |
| 12 | **gate/HANA_USDT** | **8h** | 62.59% | **62.59%** | **1** | 18.0 | 100.0 | 14.0 |
| 14 | gate/IDOL_USDT | 4h | 29.61% | **59.36%** | 2 | 13.8 | 99.2 | 11.1 |
| 18 | mexc/H_USDT | 4h | 27.89% | **56.02%** | 2 | 23.8 | 100.0 | 27.0 |

The 8h names (WET, HANA) are unmoved and act as an in-table control against the 4h names beside them.

### 4.2 Does the ranking change?

Top-20 by net APR: **14 of 20 members unchanged.** Entering: gate/GUA, gate/IDOL, mexc/GUA,
mexc/LYN, mexc/PUMPBTC, mexc/ZEST (all 4h). Leaving: gate/BTR, gate/MAV, gate/ONE, gate/POWER,
gate/TRADOOR, gate/TRUST (all 8h, pushed down rather than degraded).

**The ordering is moderately stable; the levels are not.** Anyone who read the old table as
"the best names earn ~30%" read a number that was half of the truth for two thirds of the list.

| net APR threshold | OLD count | NEW count | change |
|---|---|---|---|
| ≥ 5% | 427 | 649 | +222 |
| ≥ 10% | 173 | 356 | +183 |
| ≥ 20% | 50 | 129 | +79 |
| ≥ 30% | 18 | **85** | **+67** |
| ≥ 50% | 6 | **25** | **+19** |

Median net APR across the basis-sane universe: **3.65% → 6.16%**.

### 4.3 The basis filter is not optional — and it is a separate pre-existing problem

Before filtering, the top of the corrected ranking is **contaminated by pairs where perp and
spot are not the same asset**. Fourteen symbols with ≥2,000 cycles carry |mean basis| > 500 bps:

`mexc/EWT` **3,728,007 bps** · `gate/OPENAI` 5,959 · `gate/SIREN` −4,063 · `gate/TQQQX` −5,007 ·
`mexc/ESPORTS` −5,471 · `gate/GUA` −7,310 · `gate/ESPORTS` −1,027 · `mexc/SIREN` −1,565 · others.

`gate/ESPORTS` and `gate/SIREN` ranked **1st and 2nd** on corrected net APR before this filter.
A −4,063 bps basis means the perp trades 40% below spot — that is a broken pair mapping or a
decoupled market, not a carry opportunity. **This is a pre-existing data defect, unrelated to
the funding-interval bug, and it is reported here rather than fixed** (§8).

---

## 5. The strategic question, answered directly

### Does any coin move from "modest" into genuinely interesting territory?

**Yes, in volume.** Applying the full investability gate — ≥30% net APR, round-trip cost
≤ 50 bps, ≥90% of cycles funding-positive, breakeven ≤ 3 days, |basis| ≤ 200 bps:

| | count |
|---|---|
| OLD arithmetic | **12 names** |
| **NEW arithmetic** | **45 names** |

A 3.75× expansion of the investable set. Top survivors: mexc/BTW 135.8%, gate/龙虾 127.5%,
gate/AI 71.5%, mexc/BLUAI 71.0%, gate/WET 67.3%, mexc/LYN 65.2%, gate/HANA 62.6%,
gate/IDOL 59.4%, mexc/PLAY 56.4%, mexc/H 56.0%.

### Does the cost side still eat it?

**No — and that is the point.** Every figure above is already net of both-leg spread. The
survivors have round-trip costs of 13.8–48.1 bps against 30–136% annual carry, giving
**breakeven in under 3 days**. The doubling is not being handed back to the spread.

The cost filter does real work: of the 93 names above 30% gross-corrected net, 27 fail on
cost >50 bps (e.g. mexc/AGT at 144.7 bps, mexc/ARIA at 93.5 bps) and are excluded. A doubled
yield on a 125 bps-spread name remains uninvestable, exactly as the brief insists.

### Do BTC and the majors change?

**No. Delta is 0.00 for all of them** (§3). This is the control that proves the correction
does what it claims and nothing more.

### Does the parked verdict survive?

**Split answer, and both halves matter:**

- **"Clean stable carry is ~5–10% APR on majors" — SURVIVES, unchanged and now verified.**
  BTC 6.12%, ETH 4.15%. Those contracts are 8h and were never mis-annualised.
- **"Carry is a modest option, not the main bet" — DOES NOT survive as stated.** It
  generalised a majors-only observation to the whole strategy. Under corrected arithmetic
  there are **45 names clearing 30–136% net APR after costs, with ≥90% funding-positive
  cycles and sub-3-day breakeven** — in a 23-day bear-market sample.

**The honest framing: the strategy was never evaluated at its true yield in the tail.** That
does not automatically make it the main bet — the tail is thin, and the project's own prior
that "high-APR names are traps" still applies and is still untested against these corrected
numbers. Three of the 45 survivors (mexc/BLUAI, mexc/LAB, and mexc/CASHCAT further down) were
independently flagged **SUSPECT** by the wash-trade screen, and only 30 of the 1,197 symbols
have been screened at all. **What changed is that the tail now deserves the depth, exit-risk
and authenticity work that was previously not worth spending on a "modest" 30%.**

### The mitigating fact

**The live carry bot was never affected.** `app/carry/bot/intervals.py` fetches true intervals
from both venues and caches them — its own docstring states it exists because
`funding_basis_snapshots.funding_interval_hours` is 8 in all rows. The bot's paper selection
log shows it reasoning in `iv 4h` / `iv 8h` per name, and 5 of its 6 paper positions
(WET, HANA, IDOL, H, BTR) appear in the 45-name survivor list. **No capital decision was made
on the bad column.** The damage is to the offline write-up that produced the parked verdict.

---

## 6. `mins_to_funding` contamination

The collector derives it from 8h UTC boundaries only:

```python
# researcher/app/carry/main.py:91
def _mins_to_funding() -> float:
    """Minutes until the next 8h UTC funding boundary (00:00/08:00/16:00 UTC)."""
```

Stored range across all 7,867,128 rows is **0.0 – 479.9 minutes**, confirming an 8h ceiling
for every symbol including 4h ones.

For a 4h symbol the value is wrong whenever the true next boundary is 04:00/12:00/20:00 —
**exactly half the time**. Measured on the 129 symbols with a stored interval:

| | rows |
|---|---|
| rows with known interval | 850,314 |
| of which on 4h symbols | 626,184 |
| **of those, wrong (`mins_to_funding` > 240)** | **313,729 = 50.1%** |

The analytic prediction is 50%; the measurement is 50.1%. Extrapolating over the corrected
universe (5,174,572 rows on sub-8h symbols): **≈2,587,000 rows — 33.1% of the table — carry a
wrong `mins_to_funding`.** Where wrong, the true value is the stored value minus 240.

### Consumers that would need revisiting — list only, not revisited here

| file | role |
|---|---|
| `researcher/app/carry/main.py:91,141` | **origin** — writes the 8h-derived value |
| `researcher/app/db/neon_db.py:1184` | `_FUNDING_TIMES_SEC = (0, 28_800, 57_600)` — same 8h assumption |
| `researcher/app/db/neon_db.py:764,791` | writes `mins_to_funding` into the **ML feature set** |
| `researcher/app/core/paper_trader.py:34-43` | `_seconds_to_next_funding` — same 8h assumption, ML feature |
| `backend/app/db/ml_engine.py:165` | `ml_trade_outcomes.mins_to_funding` column |
| `backend/scripts/load_arb_dataset_to_brain.py:12,99,134` | loads it into Brain embeddings |
| `backend/app/services/brain_service.py:130,194,247` | embeds it, renders `funding_mins=` into text |
| `backend/migration/postgres/002_brain_embeddings.sql:23` | schema |

**`mins_to_funding` is an ML feature in `ml_trade_outcomes` and in the Brain embedding text.**
Any model or embedding trained on it has been fed a feature that is wrong ~a third of the
time for perps. Mitigating context: those are arb-era artefacts and arb is retired
(`ml_trade_outcomes` is a frozen 1,888-row archive), so the practical damage is likely nil —
but the list is the answer to the question asked, and nothing on it was touched.

---

## 7. Phase 2 — proposed, NOT executed

Phase 1 shows the tail conclusion changes materially, so Phase 2 is warranted. **Proposal only.**

**7.1 Fix the derivation at write time.** In `researcher/app/carry/main.py`: replace the
`FUNDING_INTERVAL_HOURS = 8` constant with a per-symbol lookup, reusing the already-working
`app/carry/bot/intervals.py` cache (it is proven — 129/129 correct). `_mins_to_funding()`
becomes `_mins_to_funding(interval_h)`, computing the next boundary on the symbol's own grid.
Refresh the interval cache on a TTL, since §1.4 shows intervals move at ~2%/23 days.

**7.2 Do NOT rewrite history — correct at read time.** Leave the 7.86M stored rows exactly as
the exchange gave them. Reasons: `funding_rate`, `perp_mark` and `spot_price` are raw and
correct, so every derived value is reproducible; a historical UPDATE would destroy the ability
to detect this class of bug again; and interval changes (§1.4) mean *no single* retroactive
value is right for the whole window anyway. Publish a read-time view instead:

```sql
-- proposal only, NOT created
CREATE VIEW v_carry_corrected AS
SELECT f.*,
       i.interval_hours AS true_interval_hours,
       f.funding_rate * (24.0/i.interval_hours) * 365 * 100 AS funding_apr_corrected_pct
FROM funding_basis_snapshots f
JOIN carry_funding_intervals i USING (exchange, symbol);   -- INNER JOIN: never impute
```
An INNER JOIN drops the uncorrectable symbols rather than silently defaulting them to 8h —
the same discipline as §1.5. This requires first back-filling `carry_funding_intervals` from
129 to full-universe coverage, which is a read-only fetch plus one INSERT.

**7.3 Prevent recurrence.** Any derived field a collector writes must be reproducible from raw
fields stored in the same row. `funding_annualized_pct` violated this: it depended on a
constant that lived only in code, so the row could not be audited. Rule: **store the input
next to the output** — persist the actual `interval_hours` used per row (and the actual
`next_settle_time`, which both venue APIs return), so a wrong derivation is detectable from
the data alone rather than by reading the collector source.

**7.4 Sequencing with prompt-53 Part B.** Both this fix and the reported-24h-volume persistence
touch `researcher/app/carry/main.py` and both require restarting `mexc-carry-collector`.
**Ship them as one change and one restart, not two.** Record a single change timestamp in
`CURRENT_STATUS.md`, and note that any analysis spanning that timestamp sees a schema/semantics
change mid-series.

---

## 8. Noted, not fixed

- **Basis contamination (§4.3):** 14 symbols with |mean basis| > 500 bps, `mexc/EWT` at
  3,728,007 bps. Almost certainly perp and spot resolving to different assets with the same
  ticker. Pre-existing, unrelated to this bug, and it silently topped the corrected ranking.
- `carry_funding_intervals` has no history and no TTL enforcement visible at table level; it
  is a point-in-time cache being read as if it were a mapping.
- `perp_depth5_usd` / `spot_depth5_usd` remain 100% NULL (carried over from prompt-51).
- `market_flow.py` untouched, as instructed.

## 9. What I could not determine

1. **The true interval history.** I have today's map plus one 23-day empirical inference for
   Gate only. For MEXC there is **no** independent check of what the interval was *during* the
   window — MEXC's ticker `funding_rate` shows no settlement discontinuity to infer from. MEXC
   corrections rest on today's API applied retroactively, ~98% reliable by the Gate analogue.
2. **Whether the 45-name survivor set is real carry or manufactured.** Only 30 of 1,197
   symbols have been through the wash-trade screen, and 3 of the survivors are already
   SUSPECT. The corrected APRs are arithmetically right; whether the funding they describe is
   payable on real volume is untested for 42 of the 45.
3. **Whether 23 days of bear-market funding generalises.** Every APR here annualises a 23-day
   window. Funding is regime-dependent, and a 136% annualised figure from 23 days is an
   extrapolation, not a forecast.

## 10. Reproduction

Read-only; `psycopg2` with `set_session(readonly=True)`. Pure-python statistics (no numpy on
this host, none installed). Live interval fetches are public GETs against Gate and MEXC.
Scripts: `recompute.py`, `final_gate.py` (session scratchpad).
The one expensive query — a `lag()` window over 7.86M rows partitioned by (exchange, symbol),
which no index covers — was run **once** (12.8 s) and cached to CSV rather than repeated.

Data: `research/reports/data/carry_corrected_ranking.csv` (1,197 rows: old value, new value,
delta, interval, costs, stability, per symbol) and `data/excluded_symbols.csv` (16 rows).
