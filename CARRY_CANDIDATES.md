# Carry bot — Phase 1 candidate screen (read-only, 2026-08-15)

**Verdict: a real farmable set exists — ~129 of 896 executable coin-venues clear every stability
gate.** Honest NET APR after real costs, on a 30-day hold with taker entry/exit:
**~10–54 %** on thin alts, **~1–4 %** on majors. With patient maker entry (realistic for carry,
which is *not* latency-sensitive) the top names land **~55–58 %**.

Two hard caveats up front, because they govern everything below: the sample is **19 days**, and
the collector **hardcodes an 8 h funding interval** — so every APR here is "at an assumed 8 h
interval, unverified per coin". And after margin, headline APR roughly **halves** (see Capital
efficiency).

---

## Step 0 — Dataset coverage

| property | value |
|---|---|
| rows | **6,522,497** |
| window | 2026-07-27 13:09 → 2026-08-15 14:28 (**19.05 days**) |
| coins | 742 (**469 dual-venue**) · venues: mexc (682), gate (529) |
| coin-venue pairs with usable book | **1,204** → 1,190 with ≥20 funding epochs |
| cadence | 300 s exactly |
| funding epochs | 58 per coin (8 h settlement) |
| max data gap | 0.92 h on every shortlist name |

NULL census on the fields we rely on:

| field | mexc | gate |
|---|---|---|
| `funding_rate`, `spot_bid/ask`, `spot_price`, `basis_bps` | 100 % | 100 % |
| `perp_bid` / `perp_ask` | 98.95 % | 100 % |
| **`perp_depth5_usd` / `spot_depth5_usd`** | **0 %** | **0 %** |

**Depth is entirely absent.** Every number in this document is therefore size-blind: it says
nothing about how much capital a name can absorb.

### Spot-leg availability — resolved structurally

`researcher/app/carry/main.py` builds its universe dynamically as *"every symbol that appears in
BOTH the perp and spot bulk feed on an exchange"* (MEXC `api/v3/ticker/bookTicker`, Gate
`/spot/tickers`). **A real spot market on the same venue is therefore guaranteed by construction
for every row** — spot-leg existence is not a question mark here, and `spot_bid`/`spot_ask` give
us its true quoted cost. What remains unknown is spot *depth* and withdrawal/borrow friction.

### ⚠ The funding-interval problem

`FUNDING_INTERVAL_HOURS = 8` is **hardcoded for both venues**; the `funding_interval_hours`
column is 8 for all 6.5 M rows and is **not venue truth**. Many MEXC/Gate perps settle every 4 h
or 1 h. Any such coin has its APR **understated here by 2× or 8×**. This cuts *toward*
conservatism for the shortlist, but it also means the trap list may under-punish some names.
**Verify the real interval per coin before allocating.**

---

## Step 1/2 — Method

- **Realized funding**, not snapshot averages: for each 8 h epoch (UTC 00/08/16, `floor(ts/28800)`)
  take the **last snapshot before settlement** as that epoch's rate. 58 epochs per coin.
- APR = mean epoch rate × 3 × 365 × 100.
- **Round-trip cost** = cross both legs at entry *and* exit = `perp_spread + spot_spread`
  (full spread on each leg), plus 4 fills of fees.
  Fees: maker mexc 1.0 bp / gate 2.0 bp (per brief); taker 5.0 bp both (**assumption — verify**).
- **NET APR = gross APR − round_trip_bps × (365/H) / 100.** Gross is never reported as take-home.
- **Basis persistence**: lag-1-day (288-snapshot) autocorrelation of `basis_bps`. High autocorr +
  large mean = the structural-basis trap that flagged ONE_USDT (~250 bps, autocorr 0.991).

### Executability split — the correction that reshaped the ranking

A first pass ranked **short-spot/long-perp** names at the top (DEXE −86 % APR, ERA −59 %). That
leg requires **borrowing and short-selling spot altcoins**, which on MEXC/Gate is generally
unavailable or expensive for thin names. Only **long-spot / short-perp** is reliably executable
with plain spot + perp accounts, so negative-funding names are reported separately and **not
ranked as winners**.

| group | count |
|---|---|
| long-spot/short-perp (**executable**) | 896 |
| short-spot/long-perp (needs spot borrow) | 278 |
| **broken pairs excluded** (\|basis\| > 500 bps) | **16** |

Broken pairs are data-integrity failures, not opportunities: `mexc/EWT` (3,660,416 bps),
`gate/VANRY` (9,897), `gate/GUA` (−7,228), `gate/OPENAI` (5,670), `mexc/ESPORTS` (−5,512),
`gate/TQQQX` (−5,005), `gate/SIREN` (−4,066). Perp and spot are not the same asset there.

---

## Step 3 — Shortlist

Gates: funding sign consistent ≥85 % of epochs · ≤0.75 reversals/wk · perp spread ≤15 bps ·
spot spread ≤20 bps · \|basis\| ≤100 bps · basis autocorr(1d) ≤0.85 · no >3 h gap · net >0 @H30.

**129 of 896 executable coin-venues pass.** Top 20, ranked by NET APR @ H=30 d, taker costs:

| venue | symbol | gross | **netH30** | netH7 | netH3 | pos% | flip/wk | perp spr | spot spr | basis | ac1d | APR h1→h2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gate | `WET_USDT` | 59.5 | **54.2** | 36.8 | 6.5 | 100 | 0.00 | 8.4 | 15.2 | 27.0 | 0.09 | 53.1 → 65.9 |
| gate | `HANA_USDT` | 57.0 | **52.4** | 37.4 | 11.3 | 100 | 0.00 | 6.9 | 10.7 | 13.7 | 0.10 | 59.6 → 54.4 |
| gate | `BTR_USDT` | 32.3 | **27.2** | 10.5 | −18.5 | 100 | 0.00 | 10.2 | 11.6 | 37.3 | 0.39 | 11.5 → 52.9 |
| mexc | `PLAY_USDT` | 27.1 | **22.0** | 5.3 | −23.8 | 100 | 0.00 | 2.9 | 19.0 | 40.9 | 0.07 | 24.9 → 29.3 |
| mexc | `H_USDT` | 27.2 | **21.9** | 4.2 | −26.6 | 100 | 0.00 | 5.6 | 18.6 | 28.5 | 0.03 | 21.6 → 33.1 |
| gate | `IDOL_USDT` | 25.7 | **21.6** | 8.1 | −15.3 | 98 | 0.74 | 8.9 | **4.8** | 10.2 | 0.14 | 11.7 → 39.6 |
| mexc | `APR_USDT` | 26.6 | **21.1** | 3.2 | −28.0 | 100 | 0.00 | 5.1 | 19.7 | 35.5 | 0.35 | 18.0 → 35.2 |
| mexc | `BULLA_USDT` | 26.1 | **20.6** | 2.6 | −28.7 | 100 | 0.00 | 5.1 | 20.0 | 7.9 | 0.42 | 40.2 → 12.0 |
| mexc | `PRL_USDT` | 21.6 | **16.5** | −0.0 | −28.8 | 100 | 0.00 | 4.7 | 16.7 | 21.7 | 0.13 | 10.4 → 32.8 |
| gate | `ELSA_USDT` | 19.4 | **14.7** | −0.8 | −27.6 | 100 | 0.00 | 5.3 | 13.3 | 13.0 | 0.10 | 23.0 → 15.8 |
| mexc | `IDOL_USDT` | 19.7 | **14.1** | −4.3 | −36.4 | 98 | 0.74 | 6.2 | 19.9 | 34.0 | 0.25 | 16.0 → 23.5 |
| gate | `AIO_USDT` | 19.4 | **13.8** | −4.5 | −36.4 | 100 | 0.00 | 9.9 | 16.0 | 23.3 | 0.06 | 20.0 → 18.9 |
| mexc | `TA_USDT` | 18.8 | **13.8** | −2.6 | −31.0 | 100 | 0.00 | 3.2 | 17.7 | 27.0 | 0.18 | 13.9 → 23.6 |
| mexc | `ACU_USDT` | 18.6 | **13.5** | −3.0 | −31.9 | 100 | 0.00 | 4.5 | 17.0 | 21.1 | 0.23 | 13.4 → 23.7 |
| mexc | `RIVER_USDT` | 18.2 | **13.4** | −2.4 | −29.7 | 100 | 0.00 | 3.9 | 15.5 | 16.2 | 0.26 | 10.2 → 26.0 |
| gate | `PTB_USDT` | 19.4 | **13.1** | −7.6 | −43.7 | 100 | 0.00 | 13.2 | 18.7 | 29.8 | 0.08 | 18.8 → 20.1 |
| gate | `STBL_USDT` | 16.5 | **11.5** | −5.0 | −33.6 | 100 | 0.00 | 8.0 | 13.3 | 41.8 | 0.20 | 7.9 → 25.2 |
| gate | `IN_USDT` | 15.9 | **11.2** | −4.0 | −30.5 | 100 | 0.00 | 8.8 | 9.3 | 9.3 | 0.30 | 7.1 → 24.9 |
| mexc | `MAGMA_USDT` | 16.8 | **11.2** | −7.5 | −40.0 | 100 | 0.00 | 7.7 | 19.0 | 19.5 | 0.09 | 15.1 → 18.6 |
| gate | `PIEVERSE_USDT` | 15.8 | **10.8** | −5.5 | −33.9 | 100 | 0.00 | 5.9 | 14.9 | 2.5 | 0.19 | 11.0 → 20.5 |

**Hold period is decisive.** Taker round-trip cost is ~38–44 bps; annualised at H=3 d that is
~48 % APR of drag. Nothing except the top two survives a 3-day hold on taker costs. This is the
single most important operational parameter.

### Cost sensitivity — maker vs taker (NET APR %)

| candidate | gross | tk H1 | tk H3 | tk H7 | tk H30 | mk H1 | mk H3 | mk H7 | mk H30 | rt cost |
|---|---|---|---|---|---|---|---|---|---|---|
| gate `WET_USDT` | 59.5 | −99.6 | 6.5 | 36.8 | 54.2 | 30.3 | 49.7 | 55.3 | **58.5** | tk 43.6 / mk 8.0 |
| gate `HANA_USDT` | 57.0 | −80.2 | 11.3 | 37.4 | 52.4 | 27.8 | 47.3 | 52.8 | **56.0** | tk 37.6 / mk 8.0 |
| gate `BTR_USDT` | 32.3 | −120.0 | −18.5 | 10.5 | 27.2 | 3.1 | 22.5 | 28.1 | **31.3** | tk 41.7 / mk 8.0 |
| mexc `PLAY_USDT` | 27.1 | −125.6 | −23.8 | 5.3 | 22.0 | 12.5 | 22.2 | 25.0 | **26.6** | tk 41.8 / mk 4.0 |
| mexc `H_USDT` | 27.2 | −134.2 | −26.6 | 4.2 | 21.9 | 12.6 | 22.4 | 25.2 | **26.8** | tk 44.2 / mk 4.0 |

**Maker entry is realistic here** — unlike ёрш, carry is not latency- or queue-sensitive; you can
work the order patiently over minutes. Treat **taker as the floor** (and as the honest cost of an
*emergency* unwind) and **maker as the ceiling** for planned entries.

### Funding decay — 19 days is short, so this was tested

- **27/129** shortlist names had lower funding in the second half.
- Median change **+0.01 pp APR**, mean **+2.13 pp**; median APR 6.91 % → 9.21 %.
- **0/129 are new listings** (all have ≥90 % of epochs present; every top name has all 58).

No systematic launch-phase decay in this window. That is reassuring but **not** proof of
persistence — it is one 19-day sample, and per-name swings are large (`BULLA` 40.2 → 12.0,
`BTR` 11.5 → 52.9). Two of the top five moved by >10 pp between halves.

### Risk detail on the top names

| candidate | funding sd | min FR | max FR | perp spr p50/p90 | spot spr p50/p90 | basis μ / σ |
|---|---|---|---|---|---|---|
| gate `WET_USDT` | 0.0423 % | **+0.0100 %** | 0.1756 % | 8.4 / 13.1 | 15.2 / 22.6 | 27.0 / 13.7 |
| gate `HANA_USDT` | 0.0495 % | **+0.0100 %** | 0.1956 % | 6.9 / 10.3 | 10.7 / 18.7 | 13.7 / 12.9 |
| gate `BTR_USDT` | 0.0337 % | +0.0020 % | 0.1260 % | 10.2 / 16.5 | 11.6 / 19.8 | 37.3 / 22.7 |
| mexc `PLAY_USDT` | **0.0180 %** | +0.0050 % | 0.0899 % | **2.9 / 5.9** | 19.0 / 26.1 | 40.9 / 12.5 |
| mexc `PRL_USDT` | **0.0168 %** | +0.0050 % | 0.0668 % | 4.7 / 8.7 | 16.7 / 25.5 | 21.7 / 10.8 |
| gate `IDOL_USDT` | 0.0292 % | −0.0654 % | 0.0938 % | 8.9 / 11.8 | **4.8 / 10.4** | 10.2 / 13.7 |

`WET`, `HANA`, `PLAY`, `PRL` **never printed a negative funding rate** across all 58 epochs.
Only 6/129 shortlist names have a p90 spot spread more than 2× the median, so tail entry cost is
mostly contained. Basis σ on the shortlist: median 10.4 bps, p90 18.0 bps — that is the size of
the unhedged mark-to-market swing between entry and exit, and it is **not** in the NET APR.

## Traps — high gross APR that fails the gates

Confirms and sharpens the earlier finding that high-APR names are traps. The dominant killer is
**wide spot spread**, then **funding reversals** — not funding level.

| venue | symbol | gross | pos% | flip/wk | perp spr | spot spr | why excluded |
|---|---|---|---|---|---|---|---|
| gate | `龙虾_USDT` | 77.6 | 90 | 3.68 | 6.4 | 11.9 | **3.7 reversals/wk** |
| mexc | `BTW_USDT` | 63.8 | 100 | 0.00 | 4.7 | 21.0 | spot spread 21 bps |
| gate | `TUT_USDT` | 54.6 | 90 | 3.68 | 6.1 | 15.9 | **3.7 reversals/wk** |
| gate | `POWER_USDT` | 48.7 | 93 | 1.47 | 4.2 | 21.0 | reversals; spot 21 bps; basis −68 |
| gate | `ONE_USDT` | 46.6 | 90 | 2.21 | 8.3 | 20.6 | 2.2 reversals/wk; spot 21 bps |
| gate | `TRUST_USDT` | 46.6 | 100 | 0.00 | **23.5** | 14.6 | perp spread 24 bps |
| mexc | `STAR_USDT` | 42.3 | 97 | 1.47 | 12.0 | **58.8** | spot spread 59 bps |
| gate | `AI_USDT` | 37.7 | 100 | 0.00 | 23.3 | 23.1 | both spreads >23 bps |
| mexc | `BROCCOLIF3B_USDT` | 37.5 | 91 | 1.47 | 7.4 | **97.0** | spot spread 97 bps |
| mexc | `ARCSOL_USDT` | 33.3 | 100 | 0.00 | 4.1 | **68.0** | spot spread 68 bps |
| mexc | `ON_USDT` | 31.0 | 100 | 0.00 | 4.1 | **74.3** | spot spread 74 bps |
| mexc | `CLO_USDT` | 31.9 | 100 | 0.00 | 8.8 | **53.8** | spot spread 54 bps |

Separately, the biggest \|APR\| names in the whole dataset are **negative-funding** and need spot
borrow — `LA` (−292 %), `KAITO` (−276 %), `HOME` (−165 %), `ACE` (−188 %), `EUL` (−112 %). Most
also flip sign constantly (KAITO 4.8–7.0 reversals/wk, 66–69 % consistency). **Not counted.**

## Sanity anchors — the clean but modest baseline

| venue | symbol | gross | netH30 | netH7 | pos% | flip/wk | perp spr | spot spr |
|---|---|---|---|---|---|---|---|---|
| mexc | `BTC_USDT` | 6.36 | **3.93** | −4.07 | 100 | 0.00 | 0.02 | 0.00 |
| gate | `BTC_USDT` | 4.06 | 1.62 | −6.39 | 81 | 5.88 | 0.02 | 0.02 |
| mexc | `ETH_USDT` | 4.00 | 1.56 | −6.48 | 91 | 3.67 | 0.05 | 0.05 |
| mexc | `BNB_USDT` | 4.69 | 1.97 | −6.96 | 74 | 2.57 | 1.69 | 0.65 |
| gate | `XRP_USDT` | 4.16 | 1.49 | −7.27 | 78 | 4.78 | 0.95 | 0.96 |
| mexc | `SOL_USDT` | 3.76 | 1.00 | −8.07 | 71 | 6.25 | 1.35 | 1.35 |

Matches the earlier finding (BTC 6–7 %). Majors have near-zero spreads but **flip sign
constantly** (BTC on gate: 5.88 reversals/wk) — they are ballast, not the engine. Across all
1,190 coin-venues: **23.9 % negative funding, 72.8 % within ±10 % APR**, median +4.14 %.

---

## Step 4 — Verdict and caveats

**Realistic farmable set:** ~129 executable coin-venues. Honest NET APR band **≈10–54 %** at
H=30 d taker; **≈26–58 %** with maker entry. Majors give **1–4 %** as a stable floor.

### Best 3–5 to start

1. **gate `HANA_USDT`** — 57.0 gross / 52.4 net H30. Tightest total cost in the top tier
   (6.9 + 10.7 bps), 100 % positive, zero reversals, never printed negative funding, low basis
   (13.7 bps). The best risk/return balance in the set.
2. **gate `WET_USDT`** — highest net (54.2), 100 % positive, zero reversals, never negative,
   funding *rose* into the second half (53 → 66).
3. **gate `IDOL_USDT`** — lowest total entry cost of any candidate (spot spread **4.8 bps**), so
   it is the one name that stays positive at short holds. Accept 25.7 gross for the flexibility.
4. **mexc `PLAY_USDT`** — most stable funding (sd 0.0180 %), very tight perp leg (2.9 bps).
5. **mexc `BTC_USDT`** — as a control/ballast leg: 3.93 % net, ~zero spread, 100 % positive.

Start with **1–2 names, small, for one funding cycle**, and verify realised funding receipts
against this model before scaling. The point of Phase 1 is a ranked list, not an allocation.

### Caveats, plainly

1. **19 days = one funding regime.** 58 epochs per coin. This window had no broad risk-off event.
   Funding regimes change with market direction; a sustained alt selloff flips these to negative.
2. **The 8 h interval is assumed, not verified.** Hardcoded by the collector. Coins on 4 h/1 h
   settlement have APR understated 2×/8× here. **Verify per coin.**
3. **Funding can flip — quantified.** Shortlist names show 0–0.74 reversals/wk, but the wider
   universe runs to 7/wk, and per-name half-over-half swings exceeded 10 pp on two of the top
   five. The gates measure the past, not a guarantee.
4. **Short-perp LIQUIDATION risk.** A thin alt can gap 50–100 % in a day. Sizing rule: run the
   perp leg at **≤2–3× effective leverage with an automated top-up trigger**, and treat any name
   whose spot spread widens past its p90 as a de-risk signal. A liquidated short leg converts a
   delta-neutral position into a naked long spot — the single worst failure mode here.
5. **Capital efficiency roughly halves the headline.** Long spot costs 1.0× notional; the short
   perp needs margin. At a prudent 2–3× on the perp leg total capital is ~1.35–1.5× notional, so
   a 54 % APR on notional is **~36–40 % on deployed capital** — and at 1× perp margin it is ~27 %.
   **The table above is APR on notional, not on capital.**
6. **Zero depth data.** `perp_depth5_usd`/`spot_depth5_usd` are 100 % NULL. Nothing here says how
   much size these names absorb. On thin alts the answer may be a few thousand dollars, which
   would make the high-APR tail irrelevant in practice. **This is the biggest open unknown.**
7. **Spot custody/withdrawal friction.** The spot leg must be held on the same venue as the perp
   short. Withdrawal suspensions, delistings and token migrations are real on thin alts and are
   not modelled at all.
8. **Basis risk is not in NET APR.** Entry-to-exit basis moves (σ ≈ 10.4 bps median, 18 bps p90)
   are unhedged P&L. Entering at a *positive* basis is favourable for long-spot/short-perp (the
   rich perp converges), so this is mildly conservative — but it can go the other way.
9. **Fee assumptions unverified.** Taker 5.0 bp both venues is a placeholder; the Gate maker rate
   should be confirmed (the −1.0 bp rebate asserted in `l2_symbols.py` is still unconfirmed).
10. **Correlated exposure.** The shortlist is overwhelmingly thin alts. Delta-neutrality hedges
    price, not funding-regime risk, which is common across the whole basket.

---

## Data integrity

Read-only. No writes, no schema changes, no service changes, no push.

| table | rows | status |
|---|---|---|
| `spread_observations` | 471,481 | **frozen, unchanged** (2026-07-06 17:34 → 2026-07-27 13:06) |
| `funding_basis_snapshots` | 6,522,497 | intact (carry collector live) |
| `ersh_book_l2` | 2,528,080 | intact (ёрш L2 collector live) |
| `tape_prints` | 1,026,212 | intact (ёрш tape collector live) |
| `book_ticker` | 601,264 | intact (ёрш tape collector live) |

Growth in the live tables is the running collectors; `spread_observations` is byte-stable.
