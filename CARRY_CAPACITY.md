# Carry capacity — four-leg book walk (**PRELIMINARY**, 2026-08-15)

> ## ⚠ The brief's premise is not met yet
> This was to run after **~1–2 days** of `carry_book_l2`. At run time only
> **12.6 minutes** existed (14:51 → 15:03 UTC, same day the collector was built).
> **The requested worst-hour / 5th-percentile-thinnest-hour analysis is impossible** —
> there is not one hour of data, let alone a day. Nothing here is a worst-hour figure.
> Where the brief asks for one, this reports the **5th-percentile snapshot inside the
> 12-minute window**, which is a dispersion measure, not a diurnal worst case.
>
> Median depth *is* genuinely measurable now, on hundreds of snapshots per leg rather
> than Part B's single one. That is what this document delivers.

**Even so, the answer is already stark and unlikely to reverse: the alt spot legs are
tiny.** Part B's smoke reading is confirmed, not corrected. The binding leg on every
alt is a **spot** leg, and it caps prudent size in the **€50–€350** range — one to two
orders of magnitude below a €30,000 target.

---

## Step 1 — Four-leg depth (USD absorbable within slippage from touch)

Slippage is measured **from the touch (level-1 price)** deliberately: the quoted
half-spreads are already charged in the Phase A round-trip cost, so measuring from the
touch gives the *incremental* cost of size without double-counting.

| venue/symbol | leg | med@10bp | p5@10bp | med@25bp | p5@25bp | med top-10 | snaps |
|---|---|---|---|---|---|---|---|
| gate `HANA` | ENTRY spot buy | 86 | 22 | 247 | 78 | 492 | 969 |
| gate `HANA` | ENTRY perp short | 2,177 | 1,073 | 4,421 | 3,580 | 5,995 | 187 |
| gate `HANA` | **EXIT spot sell** | **68** | **19** | **178** | **36** | 994 | 969 |
| gate `HANA` | EXIT perp cover | 1,405 | 196 | 4,769 | 2,101 | 4,802 | 187 |
| gate `WET` | ENTRY spot buy | 112 | 96 | 6,326 | 2,086 | 9,605 | 551 |
| gate `WET` | ENTRY perp short | 201 | 43 | 1,045 | 480 | 1,181 | 396 |
| gate `WET` | **EXIT spot sell** | 184 | 98 | **544** | **108** | 9,604 | 551 |
| gate `WET` | EXIT perp cover | 87 | 50 | 1,056 | 719 | 1,338 | 396 |
| gate `IDOL` | **ENTRY spot buy** | **61** | **36** | **61** | **36** | **61** | 360 |
| gate `IDOL` | ENTRY perp short | 650 | 447 | 963 | 751 | 993 | 375 |
| gate `IDOL` | EXIT spot sell | 75 | 57 | 75 | 57 | 75 | 360 |
| gate `IDOL` | EXIT perp cover | 390 | 108 | 1,125 | 810 | 1,125 | 375 |
| gate `BTR` | **ENTRY spot buy** | 120 | 60 | **280** | **116** | 1,596 | 663 |
| gate `BTR` | ENTRY perp short | 1,052 | 131 | 2,424 | 1,934 | 3,330 | 446 |
| gate `BTR` | EXIT spot sell | 251 | 32 | 366 | 175 | 726 | 663 |
| gate `BTR` | EXIT perp cover | 1,075 | 396 | 2,127 | 1,567 | 3,045 | 446 |
| mexc `PLAY` | **ENTRY spot buy** | 176 | 139 | **245** | **203** | **245** | 48 |
| mexc `PLAY` | ENTRY perp short | 2,091 | 1,056 | 8,458 | 6,475 | 8,458 | 451 |
| mexc `PLAY` | EXIT spot sell | 1,849 | 1,829 | 1,849 | 1,829 | 1,930 | 48 |
| mexc `PLAY` | EXIT perp cover | 1,414 | 999 | 5,004 | 3,876 | 5,010 | 451 |
| mexc `BTC` | ENTRY spot buy | 651,982 | 369,989 | 651,982 | 369,989 | 651,982 | 1195 |
| mexc `BTC` | ENTRY perp short | 3,526,364 | 1,942,431 | 3,526,364 | 1,942,431 | 3,526,364 | 1354 |
| mexc `BTC` | **EXIT spot sell** | 97,965 | 28,216 | **97,965** | **28,216** | 97,965 | 1195 |
| mexc `BTC` | EXIT perp cover | 2,881,150 | 1,581,098 | 2,881,150 | 1,581,098 | 2,881,150 | 1354 |

### Binding leg per name — it is a SPOT leg on all six

| venue/symbol | binding leg | med @25bp | p5 @25bp | ≈ EUR |
|---|---|---|---|---|
| gate `IDOL_USDT` | ENTRY spot buy | **$61** | $36 | **€57** |
| gate `HANA_USDT` | EXIT spot sell | **$178** | $36 | **€165** |
| mexc `PLAY_USDT` | ENTRY spot buy | **$245** | $203 | **€227** |
| gate `BTR_USDT` | ENTRY spot buy | **$280** | $116 | **€259** |
| gate `WET_USDT` | EXIT spot sell | **$544** | $108 | **€504** |
| mexc `BTC_USDT` | EXIT spot sell | $97,965 | $28,216 | €90,708 |

**Part B smoke confirmed.** IDOL ~$44 → **$61**; PLAY ~$203 → **$245**; HANA ~$350 →
**$178** (worse). Those single snapshots were representative.

Note the perp legs are 5–30× deeper than the spot legs on every alt. **Perp is not the
problem — spot is.** Note also that for `IDOL` and `PLAY` the med@25bp equals the med
top-10 total, meaning the *entire visible book* is exhausted before 25 bps.

## Step 2 — Round-trip slippage at real sizes (bps of notional, all 4 legs)

EUR→USD @ 1.08 (stated assumption).

| venue/symbol | €50 | €100 | €250 | €500 | €1,000 |
|---|---|---|---|---|---|
| gate `HANA_USDT` | 10.1 | 26.8 | 61.5 | **BOOK OUT** | BOOK OUT |
| gate `WET_USDT` | 14.1 | 25.8 | 51.4 | 67.6 | 82.0 |
| gate `IDOL_USDT` | 6.1 | **BOOK OUT** | BOOK OUT | BOOK OUT | BOOK OUT |
| gate `BTR_USDT` | 5.3 | 19.1 | 29.9 | 73.3 | BOOK OUT |
| mexc `PLAY_USDT` | 4.7 | 6.3 | **BOOK OUT** | BOOK OUT | BOOK OUT |
| mexc `BTC_USDT` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

"BOOK OUT" = the **visible top-10** book cannot absorb that size on at least one leg.

## Step 3/4 — Capacity-adjusted net APR and max prudent size

Net on deployed capital, L=2×, taker-at-size, H=30 d:
`net = (gross − (rt_taker + slippage) × 365/H / 100) / (1 + 1/L)`

| venue/symbol | <25 bps cap | <50 bps cap | <100 bps cap |
|---|---|---|---|
| mexc `PLAY_USDT` | €225 → **34.8 %** | €225 → 34.8 % | €225 → 34.8 % |
| gate `WET_USDT` | €75 → **34.5 %** | €225 → 32.2 % | €1,000 → 29.6 % |
| gate `HANA_USDT` | €75 → **33.4 %** | €200 → 30.9 % | €425 → 27.0 % |
| gate `IDOL_USDT` | €50 → **32.2 %** | €50 → 32.2 % | €50 → 32.2 % |
| gate `BTR_USDT` | €150 → **16.0 %** | €350 → 14.3 % | €700 → 11.9 % |
| mexc `BTC_USDT` | €30,000 → **2.6 %** | €30,000 → 2.6 % | €30,000 → 2.6 % |

`PLAY` and `IDOL` do not improve with a looser cap — their **visible book runs out**,
not their slippage budget.

**Re-ranked by capacity-adjusted net APR** (rather than headline APR), the ordering
barely changes — PLAY, WET, HANA, IDOL, BTR, BTC — but the *sizes* collapse. The
headline 35–38 % APR from Phase 2 survives only at **€50–€225 per name**.

---

## The plain answers

### Does €1,000 fit? **Yes — but only spread across the basket, or in WET alone.**

- **Across the 5 alts at <50 bps each:** €200 HANA + €225 WET + €50 IDOL + €350 BTR +
  €225 PLAY = **€1,050 at a blended ~26.5 % net APR.** This works today.
- **In one name:** only `WET` reaches €1,000, costing ~82 bps round-trip → still
  **~29.6 % net APR at H=30 d.** No other alt gets there.
- `IDOL` is effectively untradeable at any size — **€50 and the visible book is gone.**

### Can the basket absorb €30,000? **No. Not remotely — off by ~12–30×.**

The five alts together cap at **€1,050** (<50 bps) to **€2,400** (<100 bps). Everything
above that has nowhere to go but BTC at 2.6 %. Blended outcome for a €30,000 deployment:

| deployment | alts | BTC remainder | **blended net APR** |
|---|---|---|---|
| €1,000 | €1,000 @ ~26.5 % | — | **~26.5 %** |
| €2,400 | €2,400 @ ~24.5 % | — | **~24.5 %** |
| €30,000 | €2,400 @ ~24.5 % | €27,600 @ 2.6 % | **~4.4 %** |
| €30,000 (tighter <50 bps) | €1,050 @ ~26.5 % | €28,950 @ 2.6 % | **~3.4 %** |

**€30,000 in this basket is a ~3.4–4.4 % APR product — i.e. BTC carry with extra steps.**
The 35 % APR names are real, but they are a **€1–2k opportunity, not a €30k one.**

This is the same lesson as ёрш and maker-convergence in a new costume: the edge exists
and is measurable, but the market is too small for it to matter at our size. The
difference is that here it is a *capacity* ceiling rather than a microstructure one —
which means it scales with the number of *names*, not with capital per name. Getting to
€30k means ~30–60 qualifying coins, not bigger positions in these six.

---

## Caveats — read before acting

1. **12.6 minutes, not 1–2 days.** No worst-hour, no diurnal cycle, no news/volatility
   event in the sample. Thin books are worst exactly when you must exit, and this
   window contains no such moment. **Every number here is the calm-market case.**
2. **TOP-10 TRUNCATION — the single biggest limitation.** The collector stores 10
   levels. For `IDOL` and `PLAY` the *entire visible book* is smaller than €250, so
   "BOOK OUT" means **"deeper than we can see"**, not "no liquidity exists". Real
   capacity for those two is **unknown and strictly ≥ what is reported.** All capacity
   figures here are **lower bounds**. Fixing this is a one-line change (`LEVELS` in
   `depth_symbols.py`, 10 → 25) and is the prerequisite for a definitive answer.
3. **Single-venue depth only.** Spot must be held on the same venue as the perp short,
   so cross-venue liquidity does not help.
4. **MEXC spot is REST-polled**, not streamed (protobuf absent from `researcher/.venv`).
   `PLAY` spot has only 48 snapshots in the window because its book is genuinely
   near-static — verified separately, not a collector fault — but it is the thinnest
   evidence base of the six.
5. **No live queue, no fill simulation.** This is a static book walk. It assumes our
   order consumes the book as displayed, with no one else competing and no cancels.
6. **Slippage measured from the touch**, with half-spreads charged separately in the
   Phase A cost. Correct, but it means these bps must always be read *together with*
   the Phase A `rt_taker`, never alone.
7. **Maker entry would reduce the cost** but cannot be assumed at these sizes — on a
   book this thin, working an order patiently is exactly what moves it.
8. Funding APR inputs carry all their own Phase 1/2 caveats (19-day sample, one regime).

## Recommended next step

Raise `LEVELS` 10 → 25 in `researcher/app/carry/depth_symbols.py` and let it run
1–2 days. Without that, `IDOL` and `PLAY` capacity stays unmeasurable and the €30k
question keeps returning a lower bound rather than an answer. **One-line change,
awaiting your go-ahead** — I did not make it unasked, since it changes what is collected.

---

## Ops — depth collector throttled (not stopped)

**Throttled, deliberately not stopped:** stopping now would foreclose the 1–2 day
worst-hour study the brief actually wants, which is still unrun.

| | before | after |
|---|---|---|
| `_SNAP_MIN_INTERVAL` | 0.25 s (≤4/s) | **10.0 s (≤0.1/s)** |
| row rate | ~99 rows/s | **20 rows/s** |
| per day | ~8.6 M rows (~1.2 GB) | **~1.73 M rows (~240 MB)** |

Still 360 snapshots/hour/stream — ample for hourly depth percentiles. All 12 streams
(6 names × perp/spot) confirmed alive after restart; service `active`.

## Data integrity

Read-only analysis. The only change was the authorised throttle.

| table | rows | status |
|---|---|---|
| `spread_observations` | 471,481 | **frozen, unchanged** (2026-07-06 17:34 → 2026-07-27 13:06) |
| `funding_basis_snapshots` | 6,530,862 | intact, **still collecting** (last 15:08 UTC) |
| `ersh_book_l2` | 2,576,250 | intact (ёрш L2 live) |
| `tape_prints` | 1,032,763 | intact (ёрш tape live) |
| `book_ticker` | 607,226 | intact (ёрш tape live) |
| `carry_book_l2` | 167,980 | 22 MB, now throttled |

Other collectors untouched — `mexc-carry-collector` (up since Aug 7),
`mexc-ersh-l2` (Aug 14), `mexc-ersh-tape` (Aug 13), `mexc-backend` (Aug 13) all kept
their original uptimes. Nothing pushed.
