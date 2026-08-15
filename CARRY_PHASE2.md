# Carry bot — Phase 2: corrected economics + depth collector (2026-08-15)

Phase 1 (`CARRY_CANDIDATES.md`) ranked candidates on **gross APR on notional**, assuming the
collector's hardcoded 8 h funding interval, with **zero depth data**. Phase 2 fixes the first two
and starts collecting the third.

**Headline: two of the six basket names settle funding every 4 h, not 8 h — their APR was
understated ~2×.** `mexc PLAY_USDT` goes 27.10 → **58.99 %** and moves to #1; `gate IDOL_USDT`
goes 25.64 → **53.12 %**. Separately, moving from APR-on-notional to APR-on-**capital** cuts every
number by ~1.5× at 2× perp leverage. The two corrections partly cancel — but only for the 4 h names.

---

## Part A — Corrected economics (read-only)

### A1. Real funding interval vs the hardcoded 8 h

Fetched live per coin: Gate `/futures/usdt/contracts/{c}` → `funding_interval` (seconds);
MEXC `/contract/funding_rate/{s}` → `collectCycle` (hours). Funding epochs were then
**re-extracted on each coin's true grid**, so we are no longer sampling every other settlement
on a 4 h coin.

| venue | symbol | real interval | source field | pays/day | APR (old, 8h) | **APR corrected** | factor | epochs | pos% |
|---|---|---|---|---|---|---|---|---|---|
| mexc | `PLAY_USDT` | **4.0 h** | `collectCycle` | 6.0 | 27.10 | **58.99** | **2.18×** | 115 | 100.0 |
| gate | `IDOL_USDT` | **4.0 h** | `funding_interval` | 6.0 | 25.64 | **53.12** | **2.07×** | 115 | 99.1 |
| gate | `WET_USDT` | 8.0 h | `funding_interval` | 3.0 | 59.64 | 59.64 | 1.00× | 58 | 100.0 |
| gate | `HANA_USDT` | 8.0 h | `funding_interval` | 3.0 | 57.00 | 57.00 | 1.00× | 58 | 100.0 |
| gate | `BTR_USDT` | 8.0 h | `funding_interval` | 3.0 | 32.09 | 32.09 | 1.00× | 58 | 100.0 |
| mexc | `BTC_USDT` | 8.0 h | `collectCycle` | 3.0 | 6.36 | 6.36 | 1.00× | 58 | 100.0 |

The factor is not exactly 2.00× because re-extracting on the 4 h grid captures the settlements
the 8 h grid skipped, and their mean rate differs slightly from the sampled ones.

**This confirms the Phase 1 caveat was material, and it cuts both ways: the wider 1,190-coin
screen still carries this error.** Any name promoted from that screen must have its interval
checked before allocation.

### A2. NET APR on DEPLOYED CAPITAL

Deployed capital `C = S + S/L` (spot notional + perp margin at leverage L):

```
net_on_capital = (gross_APR − rt_bps × (365/H) / 100) / (1 + 1/L)
```

Capital multiple: **L=1× → 2.00× notional · L=2× → 1.50× · L=3× → 1.33×**.
Round-trip cost: maker = 4 × maker fee; taker = perp spread + spot spread + 4 × 5 bp.

**MAKER entry/exit** (realistic — carry is not queue- or latency-sensitive, so orders can be
worked patiently; this is the key difference from ёрш):

| venue/symbol | gross | rt bps | H=3d 1×/2×/3× | H=7d 1×/2×/3× | H=30d 1×/2×/3× |
|---|---|---|---|---|---|
| mexc `PLAY_USDT` | 59.0 | 4.0 | 27.1 / 36.1 / 40.6 | 28.4 / **37.9** / 42.7 | 29.2 / 39.0 / 43.9 |
| gate `WET_USDT` | 59.6 | 8.0 | 25.0 / 33.3 / 37.4 | 27.7 / **37.0** / 41.6 | 29.3 / 39.1 / 44.0 |
| gate `HANA_USDT` | 57.0 | 8.0 | 23.6 / 31.5 / 35.5 | 26.4 / **35.2** / 39.6 | 28.0 / 37.4 / 42.0 |
| gate `IDOL_USDT` | 53.1 | 8.0 | 21.7 / 28.9 / 32.5 | 24.5 / **32.6** / 36.7 | 26.1 / 34.8 / 39.1 |
| gate `BTR_USDT` | 32.1 | 8.0 | 11.2 / 14.9 / 16.8 | 14.0 / **18.6** / 20.9 | 15.6 / 20.7 / 23.3 |
| mexc `BTC_USDT` | 6.4 | 4.0 | 0.7 / 1.0 / 1.1 | 2.1 / **2.9** / 3.2 | 2.9 / 3.9 / 4.4 |

**TAKER entry/exit** (floor; also the true cost of an *emergency* unwind):

| venue/symbol | gross | rt bps | H=3d 1×/2×/3× | H=7d 1×/2×/3× | H=30d 1×/2×/3× |
|---|---|---|---|---|---|
| mexc `PLAY_USDT` | 59.0 | 41.8 | 4.0 / 5.4 / 6.1 | 18.6 / 24.8 / 27.9 | 26.9 / 35.9 / 40.4 |
| gate `WET_USDT` | 59.6 | 43.6 | 3.3 / 4.4 / 5.0 | 18.5 / 24.6 / 27.7 | 27.2 / 36.2 / 40.8 |
| gate `HANA_USDT` | 57.0 | 37.6 | 5.6 / 7.5 / 8.5 | 18.7 / 24.9 / 28.1 | 26.2 / 35.0 / 39.3 |
| gate `IDOL_USDT` | 53.1 | 33.7 | 6.1 / 8.1 / 9.1 | 17.8 / 23.7 / 26.7 | 24.5 / 32.7 / 36.8 |
| gate `BTR_USDT` | 32.1 | 41.7 | **−9.3 / −12.4 / −14.0** | 5.2 / 6.9 / 7.8 | 13.5 / 18.0 / 20.3 |
| mexc `BTC_USDT` | 6.4 | 20.0 | **−9.0 / −12.0 / −13.5** | −2.0 / −2.7 / −3.1 | 2.0 / 2.6 / 2.9 |

Note leverage **amplifies losses too** — the negative H=3 d taker cells get worse with L, because
the same loss is carried on less capital.

### A3. Re-ranked basket (net-on-capital, L=2×, H=7 d, maker)

| # | venue/symbol | **NET @L2,H7** | gross corr | gross old | iv | perp spr | spot spr | basis | sd(FR)% | min FR% | pos% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | mexc `PLAY_USDT` | **37.93** | 58.99 | 27.10 | 4h | 2.87 | 18.97 | 40.9 | 0.0196 | +0.0050 | 100.0 |
| 2 | gate `WET_USDT` | **36.98** | 59.64 | 59.64 | 8h | 8.38 | 15.20 | 27.0 | 0.0422 | +0.0100 | 100.0 |
| 3 | gate `HANA_USDT` | **35.22** | 57.00 | 57.00 | 8h | 6.90 | 10.68 | 13.7 | 0.0495 | +0.0100 | 100.0 |
| 4 | gate `IDOL_USDT` | **32.63** | 53.12 | 25.64 | 4h | 8.92 | 4.76 | 10.2 | 0.0278 | −0.0654 | 99.1 |
| 5 | gate `BTR_USDT` | **18.62** | 32.09 | 32.09 | 8h | 10.18 | 11.55 | 37.3 | 0.0332 | +0.0020 | 100.0 |
| 6 | mexc `BTC_USDT` | **2.85** | 6.36 | 6.36 | 8h | 0.02 | 0.00 | −4.8 | 0.0026 | +0.0001 | 100.0 |

`PLAY` takes #1 on the interval correction *and* the tightest perp leg in the set (2.87 bps).
`IDOL` remains #4 despite a 2.07× uplift because it is the only name that ever printed negative
funding (−0.0654 %, 99.1 % positive).

---

## Part B — Depth collector (additive, live)

**New service `mexc-carry-depth`** → new table **`carry_book_l2`**. Nothing existing was touched:
`mexc-carry-collector` (since Aug 7), `mexc-ersh-l2` (Aug 14), `mexc-ersh-tape` (Aug 13) and
`mexc-backend` all kept their original uptimes across this work.

| module | role |
|---|---|
| `researcher/app/carry/depth_symbols.py` | the 6-name basket + rationale |
| `researcher/app/carry/depth_store.py` | `carry_book_l2` writer (self-healing CREATE) |
| `researcher/app/carry/depth_collectors.py` | Gate perp WS · MEXC perp WS · spot REST poller |
| `researcher/app/carry/depth_main.py` | entry point |

**Streams.** Perp over websocket, reusing the streams verified for ёрш: Gate
`futures.order_book` payload `[contract,"10","0"]`; MEXC **`sub.depth.full`** with `limit:10` —
*not* plain `sub.depth`, which sends unsorted incremental diffs (that bug once made ONE_USDT look
like a 444 bps market against a real 15 bps spread).

**Spot is REST-polled at ~2/s per symbol** (Gate `/spot/order_book`, MEXC `/api/v3/depth`).
Reason, stated rather than hidden: MEXC's spot websocket is protobuf-framed and `protobuf` is not
installed in `researcher/.venv`. Installing it would mutate the shared venv every other collector
runs on, for no benefit — capacity needs depth *levels*, not tick-by-tick queue dynamics, so 2
polls/s is ample. Gate spot is polled identically for symmetry.

**Units.** `size` is native (perp = contracts, spot = base units); `size_usd` = `price × size ×
multiplier` for perp (Gate `quanto_multiplier` / MEXC `contractSize` via the existing
`ContractSpecs`), `price × size` for spot. Multiplier missing → `size_usd` NULL, never a
wrong-unit number. **Observed: 0 NULLs across all 6 names.**

### Smoke verification

All **24 combinations** present (6 names × perp/spot × bid/ask), 10 levels each, `bid1 < ask1`
everywhere, no NULL `size_usd`. Sample top-of-book (single snapshot, USD):

| venue | symbol | market | bid1 | ask1 | bid10 Σ$ | ask10 Σ$ |
|---|---|---|---|---|---|---|
| mexc | `BTC_USDT` | perp | 63024.7 | 63024.8 | 4,288,003 | 1,848,851 |
| mexc | `BTC_USDT` | spot | 63064.33 | 63064.34 | 93,023 | 993,203 |
| mexc | `PLAY_USDT` | perp | 0.03660 | 0.03661 | 7,588 | 4,600 |
| mexc | `PLAY_USDT` | spot | 0.03650 | 0.03653 | 1,937 | **203** |
| gate | `WET_USDT` | perp | 0.07174 | 0.07180 | 1,432 | 1,331 |
| gate | `WET_USDT` | spot | 0.07142 | 0.07159 | 7,308 | 13,740 |
| gate | `HANA_USDT` | perp | 0.03314 | 0.03318 | 6,031 | 3,666 |
| gate | `HANA_USDT` | spot | 0.033118 | 0.033130 | 1,114 | **350** |
| gate | `BTR_USDT` | perp | 0.02990 | 0.02996 | 3,178 | 3,063 |
| gate | `BTR_USDT` | spot | 0.02983 | 0.02988 | 1,736 | 1,178 |
| gate | `IDOL_USDT` | perp | 0.016812 | 0.016841 | 1,119 | 1,125 |
| gate | `IDOL_USDT` | spot | 0.016793 | 0.016795 | 76 | **44** |

⚠ **Early warning, not yet a conclusion** (single snapshots): for carry we *buy* spot, so the
**ask10 Σ$** column is the binding constraint. `IDOL` shows **$44**, `PLAY` **$203**, `HANA`
**$350** of visible spot ask depth across ten levels. If Part C confirms these as typical, the
spot leg — not funding — caps this strategy, and a €1,000 position would walk far down the book
on three of the five alts. `WET` ($13,740) and `BTC` look comfortable.

One anomaly investigated and cleared: `mexc PLAY_USDT` spot logged far fewer snapshots than the
rest. Direct measurement — 40 polls over 60 s produced only **8 distinct top-10 books** — so the
book is genuinely near-static and the store's dedupe is correct. Real market property, not a bug.

### Operating notes

- Row rate **~99 rows/s ≈ 8.6 M rows/day ≈ 1.2 GB/day** (138 B/row). Disk: 52 GB free.
  **Stop or throttle this service after the capacity study (2–3 days max)** — it is a measurement
  instrument, not a permanent collector. Throttle knob: `_SNAP_MIN_INTERVAL` in `depth_store.py`
  (currently 0.25 s = ≤4 snapshots/s per name·market, per the Phase 2 brief).
- `funding_basis_snapshots` collection continues unchanged (last row 14:53 UTC, still live).

---

## Part C — Capacity analysis (follow-up, after ~1 day of `carry_book_l2`)

Not run now; stated so it is not lost. Once ~24 h has accumulated, per name:

1. **Walk the book** on the legs we actually use: **spot ASK** (we buy spot) and **perp BID**
   (we short perp) at entry; spot BID / perp ASK at exit.
2. Compute the **€ notional absorbable at <10 bps and <25 bps slippage per leg**, as a
   distribution over the day (median / p10 / worst hour), not a single number — thin books are
   worst exactly when you need to exit.
3. Compute **round-trip slippage at €500 / €2,000 / €10,000**, both legs, entry+exit.
4. Derive **per-name capacity** and **basket capacity**, then answer the two real questions:
   can the basket hold a **€1k** start, and can it ever absorb **€30k**?
5. Feed the measured slippage back into the Part A net-on-capital table, replacing the quoted-
   spread assumption with a size-aware cost — that is the first number worth allocating against.

---

## Data integrity

Read-only for Part A. Part B is purely additive: one new table, one new service.

| table | rows | status |
|---|---|---|
| `spread_observations` | 471,481 | **frozen, unchanged** (2026-07-06 17:34 → 2026-07-27 13:06) |
| `funding_basis_snapshots` | 6,527,277 | intact, **still collecting** (last 14:53 UTC) |
| `ersh_book_l2` | 2,554,510 | intact (ёрш L2 live) |
| `tape_prints` | 1,029,885 | intact (ёрш tape live) |
| `book_ticker` | 604,292 | intact (ёрш tape live) |
| `carry_book_l2` | **new** | created by this phase |

No existing collector restarted, no schema altered, nothing pushed.
