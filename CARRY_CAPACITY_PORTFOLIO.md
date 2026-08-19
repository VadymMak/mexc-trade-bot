# CARRY PORTFOLIO CAPACITY — 61 names, worst-hour book walk

**Run 2026-08-19 03:30–03:55 UTC on trading-server. READ-ONLY** (SELECTs only; no writes, no
schema change, no service change during the analysis). Script:
`research/carry_screen/capacity_portfolio.py`. Raw output:
`research/carry_screen/portfolio_capacity_run.txt`. Per-name detail:
`research/carry_screen/portfolio_capacity.json`.

Supersedes `CARRY_CAPACITY.md` (2026-08-15), which ran on 12.6 minutes of data at top-10
truncation and said so.

---

## THE ANSWER IN FOUR LINES

1. **Max deployable at a MEXC ≤ 40% counterparty cap: ~EUR 20,800**, at **17.1% blended net APR**
   on capital (H=30d, L=2x, maker fees + worst-hour slippage). Tighten the slippage cap from
   50 bps to 25 bps and it falls to **EUR 7,650 at 19.4%**.
2. **EUR 30,000 does NOT fit under a 40% MEXC cap.** EUR 9,200 of it sits idle, so the honest
   number on the full 30k is **11.9%**, not 17%.
3. **The binding constraint at EUR 30k is the venue cap, not the order books.** The 60 carry
   names absorb EUR 199,000 at <50 bps — but EUR 186,000 of that is MEXC. **Gate is the scarce
   venue** (EUR 12,500 total). Relax MEXC to 60% and EUR 30,000 fits at 17.0%; at 100% MEXC it
   fits at 19.1%. The counterparty cap costs you both the size and ~2pp of APR.
4. **Going to the full 129 does not fix this at a worthwhile APR.** The 68 unmeasured names top
   out at **4.6%** net-on-capital *before* any slippage (median 4.0%) — at or below the 3.9% BTC
   ballast. They add Gate *room*, which is what's scarce, but only at ballast yields.

The EUR 30k target and the 30%+ APR are not simultaneously reachable on these two venues. Pick one.

---

## DATA CAVEAT THAT SHAPES EVERY PERP NUMBER BELOW

| market | window | span | levels |
|---|---|---|---|
| spot | 2026-08-15 14:51 → 2026-08-19 03:36 UTC | **84.75 h**, all 24 hours-of-day covered | 50 |
| perp | 2026-08-15 14:51 → 2026-08-15 **16:19** UTC | **1.47 h** | 50 (first ~50 min at 10) |

**Both perp websockets went silent at 2026-08-15 16:19:36 UTC (3 seconds apart) and never
reconnected.** The service stayed `active (running)` for 3½ days with two zombie sockets.

Root cause, [`researcher/app/carry/depth_collectors.py:109-111`](researcher/app/carry/depth_collectors.py#L109-L111)
and `:136-141`: a `recv` timeout does `continue` instead of forcing a reconnect, and the
heartbeat `ws.send` sits inside `suppress(Exception)`. A socket that dies without a FIN therefore
never raises, never logs, and never reconnects. `carry_depth.log` confirms it — zero reconnect
warnings after the 15:27 subscribe lines.

Consequences, stated rather than hidden:

- **SPOT legs: true diurnal worst hour, as briefed.** ~40 snapshots per hour-of-day bucket.
- **PERP legs: an 88-minute afternoon-UTC median, marked `*` throughout. Optimistic, not a worst
  case.** Where the brief asks for a four-leg worst hour, two of the four legs are a median.
- This matters less than it looks, because **the binding leg is a SPOT leg on 61 of 61 names**
  (Step 1b) — perp books are far deeper even comparing perp-median against spot-worst-hour. The
  capacity answer is a spot-liquidity answer. But the perp legs' *worst-hour* contribution to
  round-trip slippage is unmeasured, so every capacity figure here is **an upper bound on
  capacity / lower bound on cost** by that amount.

---

## METHOD

Four legs, each limited by a different book side:

| leg | book side | sampling |
|---|---|---|
| ENTRY spot buy | spot ask | worst hour-of-day |
| ENTRY perp short | perp bid | 88-min median `*` |
| EXIT spot sell | spot bid | worst hour-of-day |
| EXIT perp cover | perp ask | 88-min median `*` |

- **Slippage is measured from the touch** (level-1 price). The quoted half-spreads are already
  charged inside the Phase-A round-trip cost, so measuring from the touch gives the incremental
  cost of size without double counting.
- **Worst hour** = group snapshots by hour-of-day (UTC), take the median inside each bucket, then
  take the thinnest bucket. Buckets with <5 snapshots are dropped.
- **VWAP walk**, exact: consuming `n_j` of level `j` at per-unit slippage `f_j = (p_j-p_1)/p_1`,
  notional-weighted slippage after `N` is `Σ(n_j·f_j)/N`; the level that breaches the threshold is
  filled partially, solved exactly.
- **Economics**: interval-corrected gross APR (95/129 names settle 4h, not the 8h the collector
  hardcodes), net on **deployed capital** `C = S + S/L`:
  `net = (gross − rt_bps·(365/H)/100) / (1 + 1/L)`, `L = 2x`,
  `rt_bps = 4 × maker fee (gate 2.0, mexc 1.0 bps) + book-walk round-trip slippage`.
  That hybrid is what the brief specifies: maker fees, but you still pay impact for size.
- Spot snapshots subsampled to 1 per 5 minutes (~1,010 per name per side); perp uses all.
- EUR→USD at **1.08** (stated assumption).

---

## STEP 1 — BINDING LEG AND DIURNAL COLLAPSE

Capacity in EUR absorbable within 25 bps of slippage-from-touch. `collapse` = worst-hour ÷ median
on the binding leg; **< 0.50 flags an exit-risk name** whose book thins out badly at some hour of
the day. Full four-leg table (61 × 4 rows) is in `portfolio_capacity_run.txt`.

| venue/symbol | binding leg | med EUR | worst-hr EUR | collapse | thinnest hr |
|---|---|---:|---:|---:|---:|
| gate AIO_USDT | ENTRY spot buy | 621 | 168 | **0.27** | 17:00 |
| gate ARC_USDT | ENTRY spot buy | 330 | 214 | 0.65 | 21:00 |
| gate ARIA_USDT | EXIT spot sell | 615 | 322 | 0.52 | 04:00 |
| gate BLESS_USDT | ENTRY spot buy | 514 | 264 | 0.51 | 21:00 |
| gate BTR_USDT | ENTRY spot buy | 215 | 121 | 0.57 | 05:00 |
| gate ELSA_USDT | ENTRY spot buy | 183 | 80 | **0.44** | 04:00 |
| gate FF_USDT | EXIT spot sell | 1,065 | 386 | **0.36** | 10:00 |
| gate HANA_USDT | ENTRY spot buy | 129 | 87 | 0.67 | 09:00 |
| gate IDOL_USDT | ENTRY spot buy | 202 | 142 | 0.70 | 16:00 |
| gate IN_USDT | ENTRY spot buy | 94 | 81 | 0.85 | 01:00 |
| gate INX_USDT | ENTRY spot buy | 554 | 491 | 0.88 | 10:00 |
| gate LAB_USDT | ENTRY spot buy | 1,723 | 1,061 | 0.62 | 02:00 |
| gate LUMIA_USDT | ENTRY spot buy | 321 | 250 | 0.78 | 20:00 |
| gate LUNA_USDT | ENTRY spot buy | 173 | 117 | 0.67 | 11:00 |
| gate NAORIS_USDT | ENTRY spot buy | 318 | 197 | 0.62 | 14:00 |
| gate OPG_USDT | ENTRY spot buy | 65 | 44 | 0.68 | 11:00 |
| gate O_USDT | EXIT spot sell | 497 | 364 | 0.73 | 05:00 |
| gate PIEVERSE_USDT | EXIT spot sell | 1,492 | 1,076 | 0.72 | 07:00 |
| gate PTB_USDT | EXIT spot sell | 72 | 46 | 0.65 | 20:00 |
| gate RESOLV_USDT | ENTRY spot buy | 320 | 27 | **0.08** | 08:00 |
| gate SOPH_USDT | ENTRY spot buy | 264 | 76 | **0.29** | 05:00 |
| gate STBL_USDT | ENTRY spot buy | 322 | 217 | 0.67 | 03:00 |
| gate TAKE_USDT | ENTRY spot buy | 242 | 144 | 0.59 | 05:00 |
| gate UAI_USDT | ENTRY spot buy | 172 | 122 | 0.71 | 05:00 |
| gate US_USDT | EXIT spot sell | 275 | 156 | 0.57 | 13:00 |
| gate WET_USDT | EXIT spot sell | 2,698 | 415 | **0.15** | 23:00 |
| gate ZKP_USDT | EXIT spot sell | 36 | 19 | 0.54 | 08:00 |
| mexc ACU_USDT | ENTRY spot buy | 49 | 33 | 0.67 | 05:00 |
| mexc APR_USDT | ENTRY spot buy | 1,418 | 516 | **0.36** | 14:00 |
| mexc ARX_USDT | ENTRY spot buy | 2,797 | 2,219 | 0.79 | 05:00 |
| mexc BANANAS31_USDT | ENTRY spot buy | 1,152 | 756 | 0.66 | 23:00 |
| mexc BASED_USDT | ENTRY spot buy | 3,920 | 3,372 | 0.86 | 14:00 |
| mexc BSB_USDT | EXIT spot sell | 2,015 | 1,439 | 0.71 | 01:00 |
| mexc BTC_USDT | EXIT spot sell | 1,251,554 | 1,107,812 | 0.89 | 01:00 |
| mexc BULLA_USDT | EXIT spot sell | 382 | 199 | 0.52 | 23:00 |
| mexc B_USDT | EXIT spot sell | 1,770 | 1,610 | 0.91 | 04:00 |
| mexc COAI_USDT | EXIT spot sell | 1,422 | 1,228 | 0.86 | 09:00 |
| mexc EDGE_USDT | ENTRY spot buy | 4,348 | 4,028 | 0.93 | 23:00 |
| mexc ELSA_USDT | EXIT spot sell | 1,390 | 1,232 | 0.89 | 17:00 |
| mexc FARTCOIN_USDT | ENTRY spot buy | 2,986 | 2,639 | 0.88 | 10:00 |
| mexc FF_USDT | EXIT spot sell | 4,032 | 2,997 | 0.74 | 01:00 |
| mexc FOGO_USDT | ENTRY spot buy | 2,616 | 1,971 | 0.75 | 14:00 |
| mexc H_USDT | ENTRY spot buy | 1,357 | 1,017 | 0.75 | 15:00 |
| mexc IDOL_USDT | EXIT spot sell | 783 | 446 | 0.57 | 12:00 |
| mexc IN_USDT | EXIT spot sell | 1,085 | 920 | 0.85 | 21:00 |
| mexc MAGMA_USDT | EXIT spot sell | 1,060 | 694 | 0.65 | 11:00 |
| mexc MANTA_USDT | ENTRY spot buy | 2,481 | 2,010 | 0.81 | 15:00 |
| mexc MITO_USDT | ENTRY spot buy | 1,295 | 873 | 0.67 | 07:00 |
| mexc NIGHT_USDT | ENTRY spot buy | 178 | 7 | **0.04** | 03:00 |
| mexc PIEVERSE_USDT | EXIT spot sell | 1,633 | 1,231 | 0.75 | 04:00 |
| mexc PLAY_USDT | EXIT spot sell | 414 | 48 | **0.11** | 18:00 |
| mexc PRL_USDT | EXIT spot sell | 1,131 | 657 | 0.58 | 15:00 |
| mexc RIVER_USDT | ENTRY spot buy | 1,690 | 1,128 | 0.67 | 09:00 |
| mexc SAPIEN_USDT | ENTRY spot buy | 2,961 | 2,401 | 0.81 | 15:00 |
| mexc SKYAI_USDT | ENTRY spot buy | 2,781 | 2,366 | 0.85 | 13:00 |
| mexc SPACE_USDT | EXIT spot sell | 5,189 | 3,889 | 0.75 | 21:00 |
| mexc TA_USDT | ENTRY spot buy | 173 | 130 | 0.75 | 07:00 |
| mexc VELVET_USDT | ENTRY spot buy | 2,601 | 1,837 | 0.71 | 15:00 |
| mexc WLFI_USDT | ENTRY spot buy | 45,433 | 34,834 | 0.77 | 21:00 |
| mexc XVG_USDT | ENTRY spot buy | 1,226 | 1,047 | 0.85 | 08:00 |
| mexc ZEREBRO_USDT | EXIT spot sell | 1,176 | 866 | 0.74 | 23:00 |

### What Step 1 establishes

- **The binding leg is a SPOT leg on 61/61 names.** The six-name Phase-2 finding generalises to
  the whole universe. Perp depth is not the constraint; spot is. This is the single most useful
  structural fact in this study, and it is why the dead perp feed does not invalidate the answer.
- **9 EXIT-RISK names** collapse to under half their median capacity at their worst hour:
  `mexc NIGHT (0.04)`, `mexc PLAY (0.11)`, `gate WET (0.15)`, `gate RESOLV (0.08)`,
  `gate AIO (0.27)`, `gate SOPH (0.29)`, `gate FF (0.36)`, `mexc APR (0.36)`, `gate ELSA (0.44)`.
  Two of these — **WET and PLAY — are in the current Phase-2 starter basket**, and both are
  top-5 by APR. WET's exit book at 23:00 UTC is 15% of its median. Sizing them off median depth
  would have been a real error; every size below is worst-hour.
- Median-vs-worst-hour matters generally: the median across all names is ~0.70, i.e. **you lose
  roughly 30% of apparent capacity** to the diurnal low.

---

## STEP 2 — MAX PRUDENT SIZE AND CAPACITY-ADJUSTED NET APR

`sz` = largest **per-leg notional** (EUR) keeping worst-hour four-leg round-trip slippage under the
cap. Capital consumed = `sz × 1.5` at L=2x. `net` = net APR on capital at H=30d.
Ranked by net@50bps.

| # | venue/symbol | iv | gross | sz@25bp | net25 | sz@50bp | net50 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | gate WET_USDT | 8h | 59.5 | 95 | 37.0 | 211 | 34.9 |
| 2 | gate HANA_USDT | 8h | 57.0 | 63 | 35.3 | 116 | 33.3 |
| 3 | mexc H_USDT | 4h | 54.5 | 542 | 34.0 | 1,782 | 31.9 |
| 4 | mexc PLAY_USDT | 4h | 54.2 | 32 | 33.8 | 69 | 31.8 |
| 5 | mexc APR_USDT | 4h | 53.2 | 418 | 33.1 | 1,314 | 31.1 |
| 6 | mexc BULLA_USDT | 4h | 52.2 | 188 | 32.5 | 315 | 30.4 |
| 7 | gate IDOL_USDT | 4h | 51.3 | 78 | 31.5 | 274 | 29.5 |
| 8 | mexc PRL_USDT | 4h | 43.1 | 415 | 26.4 | 1,100 | 24.4 |
| 9 | mexc IDOL_USDT | 4h | 39.4 | 225 | 23.9 | 627 | 21.9 |
| 10 | gate PTB_USDT | 4h | 38.9 | 33 | 23.2 | 70 | 21.2 |
| 11 | gate AIO_USDT | 4h | 38.8 | 67 | 23.2 | 175 | 21.2 |
| 12 | gate ELSA_USDT | 4h | 38.7 | 41 | 23.1 | 118 | 21.1 |
| 13 | mexc TA_USDT | 4h | 37.5 | 134 | 22.7 | 209 | 20.6 |
| 14 | mexc ACU_USDT | 4h | 37.2 | 0 | — | 68 | 20.4 |
| 15 | mexc RIVER_USDT | 4h | 36.3 | 883 | 21.8 | 2,208 | 19.8 |
| 16 | mexc MAGMA_USDT | 4h | 33.7 | 264 | 20.1 | 1,044 | 18.1 |
| 17 | gate STBL_USDT | 4h | 33.0 | 152 | 19.4 | 300 | 17.3 |
| 18 | gate TAKE_USDT | 4h | 32.5 | 94 | 19.0 | 198 | 16.9 |
| 19 | gate BTR_USDT | 8h | 32.3 | 74 | 18.8 | 157 | 16.8 |
| 20 | gate IN_USDT | 4h | 31.8 | 77 | 18.5 | 160 | 16.5 |
| 21 | gate ARIA_USDT | 4h | 29.2 | 128 | 16.8 | 331 | 14.8 |
| 22 | gate BLESS_USDT | 4h | 28.8 | 185 | 16.5 | 447 | 14.5 |
| 23 | mexc VELVET_USDT | 4h | 26.8 | 1,057 | 15.5 | 2,182 | 13.5 |
| 24 | mexc BSB_USDT | 4h | 26.1 | 1,115 | 15.0 | 2,371 | 13.0 |
| 25 | mexc FARTCOIN_USDT | 4h | 24.8 | 2,614 | 14.2 | 4,781 | 12.1 |
| 26 | gate INX_USDT | 4h | 24.7 | 161 | 13.8 | 363 | 11.7 |
| 27 | mexc B_USDT | 4h | 24.2 | 1,614 | 13.8 | 2,634 | 11.7 |
| 28 | mexc ZEREBRO_USDT | 4h | 24.1 | 866 | 13.7 | 1,724 | 11.7 |
| 29 | mexc EDGE_USDT | 4h | 23.7 | 2,008 | 13.4 | 4,717 | 11.4 |
| 30 | mexc PIEVERSE_USDT | 4h | 22.6 | 790 | 12.7 | 1,773 | 10.7 |
| 31 | mexc IN_USDT | 4h | 21.2 | 829 | 11.8 | 1,812 | 9.7 |
| 32 | mexc SKYAI_USDT | 4h | 19.6 | 1,775 | 10.7 | 3,683 | 8.7 |
| 33 | gate LUMIA_USDT | 4h | 18.5 | 82 | 9.7 | 214 | 7.6 |
| 34 | gate PIEVERSE_USDT | 8h | 15.8 | 507 | 7.8 | 1,314 | 5.8 |
| 35 | mexc FF_USDT | 4h | 15.0 | 2,688 | 7.7 | 4,315 | 5.6 |
| 36 | gate OPG_USDT | 4h | 15.5 | 31 | 7.6 | 66 | 5.6 |
| 37 | gate O_USDT | 4h | 15.3 | 99 | 7.5 | 337 | 5.5 |
| 38 | mexc XVG_USDT | 8h | 14.8 | 927 | 7.5 | 1,759 | 5.5 |
| 39 | gate RESOLV_USDT | 8h | 15.2 | 0 | — | 38 | 5.4 |
| 40 | gate SOPH_USDT | 8h | 15.0 | 40 | 7.3 | 101 | 5.3 |
| 41 | mexc ELSA_USDT | 4h | 14.4 | 800 | 7.2 | 1,619 | 5.2 |
| 42 | mexc BASED_USDT | 4h | 14.2 | 2,327 | 7.1 | 4,692 | 5.1 |
| 43 | mexc COAI_USDT | 4h | 14.1 | 944 | 7.0 | 2,060 | 5.0 |
| 44 | gate US_USDT | 4h | 13.9 | 79 | 6.6 | 221 | 4.6 |
| 45 | mexc ARX_USDT | 4h | 13.0 | 1,213 | 6.3 | 2,900 | 4.3 |
| 46 | gate FF_USDT | 4h | 13.0 | 133 | 6.0 | 738 | 4.0 |
| 47 | **mexc BTC_USDT** (ballast) | 8h | 6.4 | ≥200,000 | 3.9 | ≥200,000 | 3.9 |
| 48 | gate ARC_USDT | 4h | 12.7 | 80 | 5.8 | 215 | 3.8 |
| 49 | gate NAORIS_USDT | 4h | 12.1 | 80 | 5.4 | 206 | 3.4 |
| 50 | gate LUNA_USDT | 8h | 12.0 | 114 | 5.4 | 200 | 3.3 |
| 51 | mexc SPACE_USDT | 4h | 11.2 | 1,786 | 5.1 | 4,923 | 3.1 |
| 52 | gate LAB_USDT | 4h | 11.5 | 509 | 5.0 | 1,596 | 3.0 |
| 53 | mexc WLFI_USDT | 4h | 11.0 | 20,174 | 5.0 | 57,889 | 2.9 |
| 54 | mexc MANTA_USDT | 4h | 11.0 | 1,603 | 4.9 | 3,025 | 2.9 |
| 55 | mexc MITO_USDT | 4h | 11.0 | 559 | 4.9 | 1,150 | 2.9 |
| 56 | mexc FOGO_USDT | 4h | 11.0 | 474 | 4.9 | 2,001 | 2.9 |
| 57 | mexc SAPIEN_USDT | 4h | 11.0 | 644 | 4.9 | 2,226 | 2.9 |
| 58 | gate UAI_USDT | 8h | 11.3 | 57 | 4.8 | 171 | 2.8 |
| 59 | mexc BANANAS31_USDT | 4h | 10.6 | 774 | 4.7 | 1,340 | 2.7 |
| 60 | mexc NIGHT_USDT | 4h | 26.4 | 0 | — | 0 | — | 
| 61 | gate ZKP_USDT | 4h | 18.1 | 0 | — | 0 | — |

Notes:

- **BTC capacity is `≥ 200,000` because that is the search ceiling**, not a measurement. Its real
  capacity is larger (worst-hour spot exit alone is EUR 1.1M within 25 bps).
- **`NIGHT` and `ZKP` are uninvestable at any size** — they cannot absorb even EUR 25 per leg
  within 50 bps round trip at their worst hour. NIGHT's 26.4% gross is unreachable.
- The 25 bps column is a genuinely different portfolio, not a rescaling: it drops
  `ACU` and `RESOLV` entirely and cuts most sizes by 2–3x.
- **H=7d holds are much worse** (Step 3): 50 bps of round-trip slippage amortised over 7 days is a
  26 pp APR drag versus 6 pp over 30 days. Carry here is a **30-day-hold strategy**; at 7 days
  more than half the universe goes negative.

---

## STEP 3 — PORTFOLIO ALLOCATION AND THE BLENDED NET APR CURVE

Greedy: fill highest capacity-adjusted net APR first, each name capped at its max prudent size.
**Venue cap: MEXC ≤ 40% of deployed capital** — chosen for MEXC's withdrawal/freeze reputation;
it is a risk decision, not a measurement, and the sensitivity below prices it. The cap is applied
to *deployed* capital and solved to a fixpoint (capping MEXC shrinks deployment, which shrinks the
MEXC budget again).

### Max prudent size at <50 bps round trip — the headline curve (H=30d)

| level | deployed | undeployed | names | gate% | mexc% | blended NET APR | on the full level |
|---|---:|---:|---:|---:|---:|---:|---:|
| EUR 1,000 | 1,000 | 0 | 4 | 60.0% | 40.0% | **32.9%** | 32.9% |
| EUR 2,000 | 2,000 | 0 | 6 | 60.0% | 40.0% | 30.4% | 30.4% |
| EUR 3,000 | 3,000 | 0 | 8 | 60.0% | 40.0% | 28.3% | 28.3% |
| EUR 5,000 | 5,000 | 0 | 12 | 60.0% | 40.0% | **26.0%** | 26.0% |
| EUR 7,500 | 7,500 | 0 | 17 | 60.0% | 40.0% | 24.2% | 24.2% |
| EUR 10,000 | 10,000 | 0 | 18 | 60.0% | 40.0% | **22.1%** | 22.1% |
| EUR 15,000 | 15,000 | 0 | 27 | 60.0% | 40.0% | 19.5% | 19.5% |
| EUR 20,000 | 20,000 | 0 | 32 | 60.0% | 40.0% | 17.4% | 17.4% |
| EUR 30,000 | **20,839** | **9,161** | 35 | 60.0% | 40.0% | 17.1% | **11.9%** |

### Same at <25 bps round trip (tighter execution discipline)

| level | deployed | undeployed | names | blended NET APR | on the full level |
|---|---:|---:|---:|---:|---:|
| EUR 1,000 | 1,000 | 0 | 8 | 31.5% | 31.5% |
| EUR 5,000 | 5,000 | 0 | 23 | 22.8% | 22.8% |
| EUR 7,500 | 7,500 | 0 | 32 | 19.5% | 19.5% |
| EUR 10,000 | **7,649** | 2,351 | 33 | 19.4% | 14.8% |
| EUR 30,000 | **7,649** | 22,351 | 33 | 19.4% | **4.9%** |

### Same at H = 7 days (<50 bps)

| level | deployed | names | blended NET APR H7 |
|---|---:|---:|---:|
| EUR 1,000 | 1,000 | 4 | 17.8% |
| EUR 5,000 | 4,449 | 11 | 11.5% |
| EUR 10,000 | 4,449 | 11 | 11.5% |
| EUR 30,000 | **4,449** | 11 | 11.5% |

Seven-day holds cap out at **EUR 4,449 deployed**: beyond that, every remaining name's slippage
exceeds its 7-day funding accrual. **Hold for 30 days or don't scale.**

### Total capacity of the 60 carry names (BTC ballast excluded)

| slippage cap | total | gate | mexc | names | usable under MEXC ≤ 40% |
|---|---:|---:|---:|---:|---:|
| <25 bps | EUR 80,612 | 4,589 | 76,023 | 56 | **EUR 7,649** |
| <50 bps | EUR 198,969 | 12,503 | 186,466 | 58 | **EUR 20,839** |

**Gate is the scarce venue by 15x.** MEXC has the depth; Gate has the safety.

### Venue-cap sensitivity at EUR 30,000 — this is the real trade-off

| MEXC cap | <25 bps: deployed / APR | <50 bps: deployed / APR |
|---|---|---|
| 0% (Gate only) | EUR 4,589 / 12.5% | EUR 12,503 / 9.6% |
| 20% | EUR 5,737 / 16.7% | EUR 15,629 / 14.0% |
| **40% (chosen)** | EUR 7,649 / 19.4% | **EUR 20,839 / 17.1%** |
| 60% | EUR 11,474 / 18.9% | **EUR 30,000 / 17.0%** |
| 80% | EUR 22,980 / 16.4% | EUR 30,000 / 18.7% |
| 100% | EUR 30,000 / 15.2% | EUR 30,000 / 19.1% |

Read this carefully: **EUR 30,000 fits from a 60% MEXC cap upward.** The books are not the
obstacle at 30k — your counterparty limit is. Note also that the APR is *not* monotonically worse
with more MEXC: at 80–100% the greedy can load the deep, high-APR MEXC names (`H`, `APR`, `PRL`,
`RIVER`) instead of being forced into the low-APR Gate tail. The 40% cap costs roughly **EUR 9,200
of deployment and ~2 pp of APR** versus 60%. Whether MEXC counterparty risk is worth that is your
call, not the data's.

**BTC ballast is never used** under any venue cap ≤ 100%: BTC is itself a MEXC name at 3.9%, so
every euro of MEXC allowance is better spent on MEXC carry names. Parking the remainder in BTC
does not raise the blended figure — it competes for the same scarce MEXC budget.

---

## STEP 4 — STARTER BASKET AND SCALING PATH

### Recommended EUR 1,000 starter — variant A, Gate-only (day 1)

Use this until a MEXC test withdrawal has actually cleared.

| # | venue/symbol | capital EUR | notional EUR | leverage | worst-hr rt slip | net APR H30 |
|---|---|---:|---:|---:|---:|---:|
| 1 | gate WET_USDT | 317 | 211 | 2x | 50 bps | 34.9% |
| 2 | gate HANA_USDT | 173 | 116 | 2x | 50 bps | 33.3% |
| 3 | gate IDOL_USDT | 411 | 274 | 2x | 50 bps | 29.5% |
| 4 | gate PTB_USDT | 99 | 66 | 2x | 50 bps | 21.2% |
| | **total** | **1,000** | 667 | 2x | | **31.1% blended** |

### Variant B, with the MEXC slice (only after the test withdrawal clears)

| # | venue/symbol | capital EUR | notional EUR | leverage | worst-hr rt slip | net APR H30 |
|---|---|---:|---:|---:|---:|---:|
| 1 | gate WET_USDT | 317 | 211 | 2x | 50 bps | 34.9% |
| 2 | gate HANA_USDT | 173 | 116 | 2x | 50 bps | 33.3% |
| 3 | mexc H_USDT | 400 | 267 | 2x | 50 bps | 31.9% |
| 4 | gate IDOL_USDT | 110 | 73 | 2x | 50 bps | 29.5% |
| | **total** | **1,000** | 667 | 2x | | **32.9% blended**, MEXC 40% |

The MEXC slice buys +1.8 pp. That is the price of the counterparty test — small at EUR 1,000,
large at EUR 20,000.

**Execution warning specific to this basket: WET is an exit-risk name (collapse 0.15, thinnest
hour 23:00 UTC).** Its EUR 211 notional is sized off the worst hour and must not be raised on the
strength of the median book. Do not unwind WET around 23:00 UTC if avoidable.

### Scaling path EUR 1k → EUR 30k

| capital | names | blended net APR (H30) | Gate / MEXC | what changes |
|---|---:|---:|---|---|
| EUR 1,000 | 4 | 32.9% | 60 / 40 | the four 30%+ names carry everything |
| EUR 5,000 | 12 | 26.0% | 60 / 40 | tail of 20%-ish names dilutes |
| EUR 10,000 | 18 | 22.1% | 60 / 40 | now into 10–15% names |
| EUR 20,000 | 32 | 17.4% | 60 / 40 | essentially the whole investable universe |
| EUR 30,000 | 35 | 17.1% on 20.8k deployed, **11.9% on the full 30k** | 60 / 40 | **capacity exhausted; 9.2k idle** |

APR does not decay to BTC-ballast levels before capacity runs out — on *deployed* capital it never
falls below ~17%. What breaks at EUR 30k is not the yield, it is the **fill**.

### Does reaching EUR 30k need the full 129? No — and that is the bad news

The 61 studied names are the **top 60 by pre-capacity net30cap plus BTC**. The 68 unmeasured names:

- best is **4.6%** net-on-capital (`gate XPIN`, `gate KGEN`, `gate AR`), **median 4.0%, min 1.6%** —
  and those figures are *before* any slippage haircut, which above costs 2–6 pp.
- 41 of the 68 sit above the 3.9% BTC ballast; **26 of those are Gate names**.

So the full 129 would relieve the *venue* constraint — 41 more Gate names is exactly the scarce
resource — but only at ballast-grade yields. A rough arithmetic scenario (**extrapolation, not a
measurement**: assumes the unmeasured Gate names average the same ~EUR 460 of capital capacity as
the measured Gate names): +41 Gate names ≈ +EUR 19k of Gate room, taking Gate to ~EUR 31k, which
under a 40% MEXC cap supports ~EUR 52k deployed. Blended would be roughly
`20.8k @ 17.1% + 9.2k @ ~4%` ≈ **13% at EUR 30,000**.

**The shortfall is therefore structural, not a sampling artefact.** EUR 30,000 at anything near
30% APR does not exist on Gate + MEXC. The honest options are:

1. **EUR 20,800 at 17.1%** with MEXC ≤ 40% — the recommended ceiling on these two venues.
2. **EUR 30,000 at ~17%** by relaxing the MEXC cap to 60%.
3. **EUR 30,000 at ~13%** by extending to all 129 names and keeping MEXC at 40% (unverified).
4. **Add venues.** The concentration is the problem: two exchanges, one of which you don't trust
   with size. This is the only path that scales without either lowering APR or raising
   counterparty risk.

---

## CAVEATS

1. **Perp legs are an 88-minute median, not a worst hour** — the collector's zombie-socket bug.
   Two of four legs are unmeasured at their diurnal low. Capacity figures are upper bounds.
2. **61 of 129 names.** Total capacity here is a **lower bound** on the universe's — though
   Step 4 shows the remainder is low-yield.
3. **~3.5 days of spot data, one sample of each hour-of-day per day.** "Worst hour" rests on ~40
   snapshots per bucket and 3–4 distinct days. A worse hour surely exists.
4. **Single-venue depth per name.** No cross-venue routing; the spot leg must clear on the same
   venue as the perp leg.
5. **No live queue, no latency, no partial-fill dynamics.** This is a book walk against a static
   snapshot: it assumes the book is there when you arrive. It is not a fill simulator, and the ёрш
   study is the standing reminder that queue reality is worse than book reality.
6. **Maker fees with taker impact** is a hybrid. Pure-maker execution would avoid the slippage but
   incur queue and adverse-selection risk that is not modelled anywhere in this document.
7. **Funding is assumed to persist.** Gross APR is a trailing ~19-day mean of realised funding at
   the corrected interval. High-APR alt funding is famously unstable; a 30-day hold is a 30-day
   bet that it does not flip.
8. EUR/USD at 1.08, flat.

---

## OPS PERFORMED

- **`mexc-carry-depth` STOPPED and DISABLED** after the analysis (authorised in the brief). Its
  measurement job is done: 25.2M rows, 3.3 GB, 61 names, 84.75 h of spot depth. Leaving 61 names ×
  50 levels running costs ~0.9 GB/day for data we now have.
- **No other service touched.** `mexc-backend`, `mexc-frontend`, `mexc-carry-collector`,
  `mexc-ersh-tape`, `mexc-ersh-l2` all still active; `mexc-researcher` still inactive+disabled.
- **Frozen archives byte-stable**: `spread_observations` 471,481 · `ml_trade_outcomes` 1,888 ·
  `paper_positions` 1,900.
- **Live collectors advancing**: `funding_basis_snapshots` 7,742,640 · `tape_prints` 2,301,791 ·
  `ersh_book_l2` 8,668,780, all writing at run time.

### Recommended next (NOT done — needs authorisation, and a code change)

A **perp-only re-run** would close the one real gap here: fix the zombie socket first (force a
reconnect on `recv` timeout, and add a staleness watchdog per stream), then collect perp depth
only for the ~35 names that are actually investable. That is ~90 MB/day, not 1 GB, and 24 h of it
would convert every starred perp figure in this document into a real worst hour. Given that spot
binds on 61/61, this would refine the numbers rather than overturn them.
