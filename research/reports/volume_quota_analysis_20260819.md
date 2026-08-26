# The $100k quota, FRONG's history, and what the 16 deaths actually were

**Date:** 2026-08-19 · **Analyst:** Claude (session on `trading-server`) · §7 of prompt-56
**Status:** READ-ONLY. No writes, no schema change, no service touched.

**Sample regime: collected 2026-07-27 → 2026-08-19 (23 days) during a bear market — BTC topped
$126,199 in Oct 2025 and traded near $60,000 in mid-2026, a >50% drawdown. Thin-name liquidity
is depressed market-wide in this window. One regime, one sample.**

---

## Headline

1. **It is a quota, and it is metered per-second, not reconciled per-day.** Five MEXC names
   have a coefficient of variation of **daily** USD volume between **0.009 and 0.031** against a
   clean-name median of **0.500** — a 20–50× separation. Their hourly share of daily volume is
   flat at 3.88–4.32% (uniform = 4.167%) in every hour of every day. There are no catch-up
   bursts because nothing needs catching up: the emitter runs at a constant rate.
2. **All 16 disappearances are CONFIRMED_DELISTING — the perp contract died in every case.**
   Zero were spot-only exits. **11 of 16 had a spot pair still trading**, so the carry pair died
   because the *contract* was delisted, not because the token left.
3. **Gate publishes `in_delisting: true`** on the per-contract endpoint. It is definitive, but
   Gate removes flagged contracts from the bulk feed at the same time — so it confirms a death,
   it does not forecast one from a universe scan.
4. **FRONG does not support the listing-obligation hypothesis.** Tape collection began 10 days
   after it listed, so the critical window is unobserved, and the spread evidence points to the
   synthetic pattern starting ~6 days *after* listing, not at it.

---

## §7.1 — Is it a quota?

### Daily observed USD volume (tape, contract-multiplier corrected; partial first/last days dropped)

| symbol | 08-14 | 08-15 | 08-16 | 08-17 | 08-18 | **CV** |
|---|---|---|---|---|---|---|
| mexc/AURASOL | 99,337 | 97,734 | 98,304 | 99,025 | 100,156 | **0.009** |
| mexc/INDEX | 101,091 | 98,456 | 99,691 | 100,193 | 101,275 | **0.010** |
| mexc/FRONG | 100,397 | 100,346 | 103,183 | 100,513 | 104,623 | **0.017** |
| mexc/WISHBONE | 104,175 | 99,704 | 100,118 | 100,334 | 99,054 | **0.018** |
| mexc/KET | 99,922 | 101,135 | 107,554 | 102,333 | 98,228 | **0.031** |
| mexc/HOODRAT | 110,724 | 108,974 | 127,954 | 102,333 | 106,951 | 0.079 |

### The core test — dispersion

| group | n | median CV | range |
|---|---|---|---|
| **DISQUALIFIED (manufactured)** | 8 | **0.025** | 0.009 – 0.212 |
| CLEAN | 12 | **0.500** | 0.207 – 0.818 |
| SUSPECT | 8 | 0.631 | 0.245 – 1.015 |

**A CV of 0.009 means daily volume varies by under 1% day-over-day.** No market does that.
AURASOL's five-day range is $2,422 on a ~$99,000 mean. The clean population's *tightest* name
(gate/CHILLGUY, 0.207) is still 23× more dispersed than AURASOL.

The two weakest members of the DISQUALIFIED group are `mexc/ENJ` (0.195) and `mexc/CATI` (0.212),
which sit inside the clean range. That is consistent with CATI having been flagged in the
authenticity report as the weakest disqualification, and it is independent corroboration of that
caveat rather than new evidence against it.

### Shortfall days

Only **one** day in 25 symbol-days fell more than 2% short of $100,000:
**`mexc/AURASOL_USDT`, 2026-08-15, $97,734 (−2.27%)**. Every other observation across all five
names lands between −2.3k and +7.6k of the round number.

### Is the printing rate modulated as the window closes? No — and that is the finding

Hourly share of daily volume, averaged over 08-14 → 08-18:

| symbol | 00h | 04h | 08h | 12h | 16h | 20h | 22h | 23h |
|---|---|---|---|---|---|---|---|---|
| mexc/AURASOL | 4.18 | 4.25 | 4.20 | 4.23 | 3.88 | 4.15 | 4.14 | 4.14 |
| mexc/FRONG | 4.07 | 4.22 | 4.32 | 4.12 | 4.01 | 4.29 | 4.09 | 4.15 |
| mexc/WISHBONE | 4.18 | 4.23 | 4.23 | 4.08 | 3.93 | 4.20 | 4.14 | 4.10 |
| *gate/LA (clean)* | 5.65 | 4.61 | **2.77** | 4.93 | **6.83** | 2.52 | 2.37 | 3.20 |
| *mexc/JIMOTHY (clean)* | 3.16 | 5.25 | **2.91** | 4.00 | **6.88** | 3.58 | 5.83 | 5.92 |

A uniform day is 4.167%/hour. The three quota names never leave **3.88–4.32%** in any hour
sampled. The clean controls swing **2.37–6.88%** — real diurnal structure.

**Mechanism: a constant-rate emitter, not a daily target with reconciliation.** There are no
catch-up bursts and no end-of-window throttling because a fixed rate produces the daily figure
by construction. This is the same object seen from a different angle as the Uniform(6,16)s
inter-arrival spacing in the authenticity report: one generator, one constant rate.

### Is $100k MEXC-specific?

**Yes, in this sample.** Every name with CV < 0.05 is MEXC (all five). The lowest CV on Gate is
**0.207** (gate/CHILLGUY) — an order of magnitude higher and inside the organic range. No Gate
name shows volume pinning at any round number.

Caveat: the tape covers 15 Gate and 15 MEXC symbols selected for the ёрш study, not a random
sample of either venue. "MEXC-specific" means "present on MEXC and absent on Gate **among these
30 names**", not a venue-level rate.

---

## §7.2 — FRONG: does synthetic flow start at listing?

`mexc/FRONG_USDT` listed **2026-08-03** (NEW_LISTING) and is DISQUALIFIED as manufactured.

### The decisive limitation, stated first

**Tape collection began 2026-08-13 — ten days after FRONG listed.** The window that would
answer the question directly is not in the data. Volume history does not help either: the
collector only began persisting reported volume at **2026-08-19T12:59:25Z** and there is no
backfill. **I cannot confirm whether volume was pinned at $100k from day one.**

### What the carry snapshots do show (spread history, available from listing)

| day | perp spread bps | spot spread bps | basis bps |
|---|---|---|---|
| 08-03 (listing) | 85.2 | 161.0 | 13.5 |
| 08-04 | 66.5 | 130.7 | 21.1 |
| 08-05 | 74.0 | 134.6 | 18.0 |
| 08-06 | 177.6 | 125.5 | 71.8 |
| 08-07 | 137.0 | 120.9 | 17.4 |
| 08-08 | 92.9 | 97.4 | 22.2 |
| **08-09** | **277.5** | 110.3 | −36.4 |
| 08-12 | 290.6 | 98.0 | 31.6 |
| 08-15 | 357.1 | 80.0 | −22.7 |
| 08-18 | 251.8 | 89.2 | 66.2 |

Two opposite movements. The **spot** spread narrows steadily (161 → ~85 bps), the normal
maturing of a new listing. The **perp** spread roughly quadruples, crossing 250 bps on
**2026-08-09**.

The manufactured signature requires prints sitting *inside* a wide perp spread. That geometry
did not exist before 08-09: for the first six days the perp spread was 66–178 bps. **The
evidence therefore points to the synthetic pattern beginning around 08-09 — six days after
listing — not at listing.**

That **weakens** the listing-linked-obligation hypothesis rather than supporting it. It is an
inference from spread geometry, not a direct observation of the flow, and the direct
observation is unavailable.

### What the tape does show, from the first full day it exists

| day | prints | USD volume | % alternating | mean gap s | p05 | p95 | distinct sizes |
|---|---|---|---|---|---|---|---|
| 08-14 | 7,953 | 100,397 | 51.0 | 10.86 | 6.0 | 16.0 | 85 |
| 08-15 | 7,890 | 100,346 | 51.4 | 10.95 | 6.0 | 16.0 | 93 |
| 08-16 | 8,047 | 103,183 | 51.3 | 10.74 | 6.0 | 16.0 | 99 |
| 08-17 | 7,958 | 100,513 | 51.2 | 10.86 | 6.0 | 16.0 | 92 |
| 08-18 | 8,118 | 104,623 | 50.8 | 10.64 | 5.9 | 16.0 | 166 |

Fully formed from the first complete day of observation: volume pinned, sides at a coin flip,
gaps Uniform(6,16)s, ~90 distinct sizes. Whatever switched it on had already finished by 08-14.

---

## §7.3 — Contingency: lifecycle class × authenticity verdict

Symbols present in **both** datasets: **23** (the tape screen covers 30, but 7 ёрш names are not
in the carry perp∩spot universe and so have no lifecycle class).

| | CLEAN | DISQUALIFIED | SUSPECT | total |
|---|---|---|---|---|
| CONTINUOUS | 10 | 7 | 5 | 22 |
| NEW_LISTING | 0 | **1** | 0 | 1 |
| **DISAPPEARED** | **0** | **0** | **0** | **0** |
| total | 10 | 8 | 5 | 23 |

Every cell named, per instruction:

- CONTINUOUS × CLEAN — **n=10**: gate CHILLGUY, KOMA, BMT, OKB, MYX, FHE, ONE, LA; mexc JIMOTHY, ANSEM
- CONTINUOUS × DISQUALIFIED — **n=7**: mexc CATI, ENJ, WISHBONE, KET, HOODRAT, INDEX, AURASOL
- CONTINUOUS × SUSPECT — **n=5**: mexc LAB, CASHCAT, ROBO, ONE, BLUAI
- NEW_LISTING × DISQUALIFIED — **n=1**: mexc FRONG
- **DISAPPEARED × anything — n=0**

**No rate is computed from this table.** With most cells at n ≤ 7 and one cell at n=1, it is a
census of 23 symbols, not an estimate of anything.

**The most important cell is the empty one.** Not one of the 16 disappeared symbols was ever
tape-screened, so **the question "do manufactured names die more often?" cannot be answered at
all** from the current data. It is not a weak result; it is no result.

---

## §7.4 — Confirming the 16 deaths

The lifecycle classification previously rested on data *shape* (round-hour timing, 3- and
5-symbol batches, VANRY and VIC dying on both venues). Probing the venues directly settles it.

### Probe design — the naive version is wrong

A first pass called every symbol "responds = true" and classified all 16 UNCONFIRMED. Inspecting
the payloads showed why:

- **MEXC `/contract/funding_rate/{sym}` keeps answering for dead contracts** — it returned a
  full body for VANRY and ACX with `success: true`. It is a zombie endpoint and a useless
  liveness probe. `/contract/ticker?symbol={sym}` is the correct one: it returns
  `{"success":true,"code":0}` **with no `data` field** for a dead contract.
- **Gate `/futures/usdt/contracts/{sym}` returns HTTP 200 for dead contracts** but carries an
  explicit **`in_delisting: true`** flag (`false` for BTC).

### Result

| classification | n |
|---|---|
| **CONFIRMED_DELISTING** | **16** |
| CONFIRMED_SPOT_ONLY | 0 |
| STILL_TRADING | 0 |
| UNCONFIRMED | 0 |

All four Gate names carry `in_delisting: true` today. All twelve MEXC names are absent from
`contract/detail` and return no `data` from the ticker.

**Which leg died:**

| | n |
|---|---|
| perp gone, **spot still trading** | **11** |
| perp and spot both gone | 5 |

### What this changes

- **The lifecycle classification is now confirmed, not inferred.** The batch-on-round-hour
  reasoning was right, and the 12.3%/yr death base rate describes genuine contract delistings.
- **The LGD numbers describe the right event.** A perp delisting is exactly the forced-deadline
  exit the loss model assumes. It is not a data artifact and not a spot-side exit.
- **The dominant mode is contract delisting with the token still alive** (11 of 16). For a
  carry pair that is the worst shape: the hedge leg is force-closed on a schedule while the
  other leg keeps trading.
- **Gate's `in_delisting` is definitive but not predictive from a universe scan.** Gate removes
  flagged contracts from the bulk feed at the same moment it flags them — which is *why* the
  collector saw them vanish. Zero of the 938 contracts in the bulk list carry the flag right
  now, and no survivor is flagged. To use it as a warning you must poll per-contract for names
  you already care about; it will not surface a candidate before it disappears.
- **MEXC has no equivalent.** `contract/detail` exposes `state`, which is `0` for all 1,121
  contracts — no delisting signal at all.

---

## What I could not determine, and why

1. **Whether FRONG's flow was synthetic at listing.** Tape starts 10 days late and volume
   history starts today. The spread evidence suggests it began ~08-09, but that is an inference
   from geometry, not an observation of flow. **This is the central question of §7.2 and it is
   unanswered.**
2. **Whether manufactured names die more often.** Zero of the 16 deaths were tape-screened
   (§7.3). The cell is empty, so no association can be measured in either direction.
3. **Whether $100k is a venue-wide MEXC policy.** The 30 tape symbols were chosen for the ёрш
   study, not sampled. All I can say is that five of fifteen MEXC names show it and zero of
   fifteen Gate names do.
4. **What the quota is *for*.** The data shows a constant-rate emitter producing ~$100k/day.
   It cannot distinguish a listing-agreement volume floor from a market-making contract from
   an exchange-run inflation programme. Motive is not in the tape.
5. **Whether the five names share one operator.** Their parameters are strikingly similar
   (Uniform(6,16)s, coin-flip sides, ~$100k/day) but I did not test for shared fingerprints —
   e.g. correlated print timestamps across symbols, which would distinguish one bot running five
   markets from five independent bots configured alike. That is a cheap follow-up.
6. **How long the pattern has run.** The tape window is 6 days. Whether these names have always
   been synthetic, or switched on at some point, is outside the data for all of them except
   HOODRAT (which visibly transitioned) and FRONG (whose transition is inferred).
7. **Whether a delisting announcement preceded the vanish.** I confirmed *state* (the contract
   is delisted) but did not retrieve announcement *dates*, so the lead time between announcement
   and contract removal — which determines how much warning a position would actually get — is
   still unmeasured.

---

## Deliverables

- `research/reports/data/daily_volume_by_symbol.csv` — 30 rows, daily USD volume + CV
- `research/reports/data/lifecycle_x_authenticity.csv` — 23 rows, the contingency census
- `research/reports/data/delisting_confirmation.csv` — 16 rows, per-symbol probe result
