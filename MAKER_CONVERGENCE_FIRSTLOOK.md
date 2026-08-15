# Maker perp-perp convergence — first look (read-only, 2026-08-15)

**Bottom line: NOT PROVEN ALIVE, and NOT cleanly dead either.** The taker version died at
−214 bps because crossing cost is unambiguous and huge. The maker version does *not* die that
way — but the central premise of the maker play, *"we EARN the spread instead of paying it"*,
is **empirically false on these books**. Measured against 661,177 real passive fills, posting at
the touch returns roughly **zero** after adverse selection: MEXC coins earn a median 20.87 bps
half-spread at fill and keep **−1.29 bps** 60 s later. The spread you think you're collecting is
exactly the spread that gets taken off you.

What remains after stripping that fantasy out is a small residual convergence edge
(**≈ +13 bps/trade at K=30**, mid-fill, before leg risk) that is **inside the error bars of the
fill model** and is dwarfed by per-coin dispersion in adverse selection. **This archive cannot
resolve it.** A targeted dual-venue tick+tape collector is required — same shape as the ёрш L2 one.

---

## Step 1 — Inventory verdict

### `spread_observations` (the frozen arb archive) — **insufficient on its own**

| property | value |
|---|---|
| rows / coins | 471,481 / 209 |
| window | 2026-07-06 17:34 → **2026-07-27 13:06** (frozen, verified unchanged) |
| per-leg bid/ask | **NO** — only `mark_spread_bps`, `executable_spread_bps`, `exec_vs_mark_edge_bps` |
| funding overlap | **NONE** — `funding_basis_snapshots` starts 2026-07-27 13:09, three minutes *after* this table ends |

Two independent blockers: no per-leg book to post against, and **zero temporal overlap with
funding**. A maker-fill sim on this table alone is impossible. That was the stated STOP condition —
for *this table* it is met.

### But a second dataset does carry per-leg books: `funding_basis_snapshots`

| property | value |
|---|---|
| rows | 6,512,937 · 682 mexc / 529 gate coins · **469 dual-venue** |
| window | 2026-07-27 → 2026-08-15 (19 days, still live) |
| cadence | **300 s exactly** (verified: 231/287 gaps == 300 s) |
| per-leg | `perp_bid`, `perp_ask`, `perp_mark`, `funding_rate`, `funding_interval_hours` — **all populated**, identical timestamps across venues (clean joins) |
| **depth** | **`perp_depth5_usd` and `spot_depth5_usd` are 100% NULL** |

So: divergence series ✅, funding ✅, **book depth ❌, tape ❌, intra-5-min ❌**.

### `book_ticker` + `tape_prints` (ёрш) — tick + tape, but single-venue

30 coin-venues, Aug 13–15, tick-level book **and** tape. This is the only data that can model a
fill honestly. **But each coin is collected on only ONE venue** — exactly one symbol
(`ONE_USDT`) has both legs. n=1 is not a cross-venue strategy test.

### Conclusion

An honest **maker-fill convergence backtest is NOT possible** on this archive. The divergence and
funding halves are computable; the **fill** half is not. Rather than fake it, I computed a
perfect-fill upper bound, then used the ёрш tape to measure — on real fills — how much of that
bound survives contact with adverse selection.

---

## Step 2 — What I actually computed

### 2a. Perfect-fill upper bound (FBS, 467 coins) — deliberately absurd

Assumes every posted order fills at the touch, unlimited size, zero queue, zero adverse selection.

| K (bps) | T (h) | n | win% | mean net bps | median hold | conv% |
|---|---|---|---|---|---|---|
| 20 | 4 | 50,826 | 97.8 | +43.69 | 0.50 h | 80.8 |
| 30 | 8 | 19,197 | 98.8 | +62.09 | 0.67 h | 84.1 |
| 50 | 8 | 5,226 | 98.5 | +90.85 | 0.83 h | 76.3 |
| 100 | 24 | 530 | 98.5 | +186.03 | 0.42 h | 90.0 |

98% win rates are a red flag, not a result. Note the *only* liquid coin in the tail,
`BNB_USDT`, is the single worst performer (−2.05 bps, 25% win). Edge that grows as liquidity
falls is the signature of a measurement artifact.

### 2b. Decomposition — where does the "edge" come from?

`gross = mid-to-mid convergence + spread capture`

| K | T | gross | mid_conv | spread_cap | % from spread |
|---|---|---|---|---|---|
| 20 | 4 | 49.63 | 25.55 | 24.08 | **48.5%** |
| 30 | 8 | 67.94 | 39.97 | 27.98 | **41.2%** |
| 50 | 8 | 96.55 | 63.38 | 33.17 | 34.4% |

Bucketed by quoted spread width, `spread_cap` scales directly with book width
(8.5 bps for <5 bps-spread coins → **98.5 bps** for >40 bps-spread coins). Roughly **half the
upper bound is the free-fill assumption**, and it is concentrated exactly where fills are worst.

### 2c. The frozen archive already measured this illusion

`spread_observations` stored mid-based *and* book-walked spreads on the same rows:

| mark (mid) spread | avg **executable** spread | median | % positive | avg illusion |
|---|---|---|---|---|
| 30–50 bps | **−141.2 bps** | −18.6 | 12.4% | 177.9 |
| 50–100 bps | **−212.5 bps** | −69.8 | 24.3% | 284.4 |

A 36.7 bps *mid* divergence is a **−141 bps** executable spread. On thin books the mid is a fiction.

### 2d. HONEST MAKER MARKOUT (ёрш tape) — the decisive measurement

Rest at the touch; count a fill **only when a tape print actually trades through** the level
(sell-aggressor ≤ our bid, buy-aggressor ≥ our ask). Then mark out against the future mid.
**661,177 real fills, 29 coin-venues.**

| venue | coins | median half-spread earned | markout @10 s | @60 s | @300 s | **net @60 s after fee** |
|---|---|---|---|---|---|---|
| MEXC | 15 | **20.87 bps** | −4.55 | −1.29 | −4.22 | **−2.29** |
| Gate | 14 | **8.86 bps** | −0.13 | +0.76 | +1.82 | **−1.24** |

**Only 11 of 29 coin-venues are net-positive.** Adverse selection consumes ~100% of the quoted
half-spread. Dispersion is brutal: `HOODRAT` −84.9, `INDEX` −81.1, `JIMOTHY` −31.2 bps per fill,
versus `FRONG` +66.4. This kills the `spread_cap` component of 2b outright — it is worth ≈ 0,
not +24…+41 bps.

One useful consequence: since markout ≈ 0, **"fill at mid" is approximately the empirically
correct neutral assumption** — the half-spread earned and the adverse move that follows roughly
cancel. That makes the mid-fill number the most defensible estimate available.

### 2e. Execution-lag test — is the mid convergence even real?

Delay execution by one 5-min snapshot. Real slow basis convergence should barely care; noise
reverting inside a wide book disappears.

| K | lag 0 | lag 1 (5 min) | lag 2 | lag 3 | decay 0→1 |
|---|---|---|---|---|---|
| 20 | 28.22 | 14.74 | 13.61 | 13.01 | **−47.7%** |
| 30 | 39.58 | 19.03 | 17.61 | 16.78 | **−51.9%** |
| 50 | 62.73 | 26.10 | 24.18 | 23.32 | **−58.4%** |
| 100 | 126.36 | 42.52 | 36.97 | 34.73 | **−66.4%** |

**Half to two-thirds of the apparent convergence is gone within 5 minutes** — that is the
mid-measurement noise reverting, not a price converging. Critically, the curve then **flattens**
(lag1 ≈ lag2 ≈ lag3): a genuine slow-converging residual of ~13–19 bps does survive at K=20–30.

Honest net at K=30, lag 1, mid-fill, after 6 bps fees: **≈ +13 bps/trade.**

### 2f. Anton's "spread ≥ 3× funding" filter is a **no-op** in this data

Median net funding differential between venues is **0.31 bps per 8 h** (p90 2.89, p99 13.64).
Realised holding periods are 0.3–1.6 h, so funding contributed **0.03–0.87 bps per trade** — noise.
The filter requires spread ≥ ~0.93 bps and is satisfied by literally every K ≥ 20 candidate.
**Funding drag did not eat the edge here. It never got a chance to.** The filter would only bind
on multi-day holds, which this strategy never takes.

---

## Step 3 — Verdict

**Maker convergence is not killed the way taker was.** Taker died on a mechanical, unavoidable
−214 bps crossing cost. Maker instead dies — or fails to be provable — on **adverse selection**,
which is empirically ~100% of the quoted half-spread on exactly the thin coins where the
divergence signal appears.

After removing both fantasies (free spread capture, instant execution on a noisy mid), what is
left is **≈ +13 bps/trade at K=30 with 5-min-lagged mid fills**. Against 6 bps of fees that is a
real but *thin* margin — and it has not survived the two largest untested risks (leg risk,
size/depth). **I do not consider this validated.** It is "worth one targeted collector", not
"worth capital".

### Every simplification that could still be optimistic

1. **Leg risk is entirely unmodelled — the biggest one.** Every result assumes both legs fill.
   In reality one leg fills and the other doesn't, leaving a naked directional position on a thin
   altcoin. Real maker convergence is dominated by this; nothing here touches it.
2. **No queue position.** I assume a trade-through fills us. In reality we're behind existing
   size and often *don't* fill on the print that would have been profitable — while still
   filling on the ones that run us over. This biases markout **optimistic**.
3. **Zero size/depth realism.** `perp_depth5_usd` is 100% NULL across the entire FBS archive. Every
   bps figure assumes unlimited clip at the touch. On coins quoting 20–40 bps wide, real depth may
   be a few hundred dollars — the +13 bps could be unreachable at any meaningful notional.
4. **Markout ≈ 0 is a median across wildly dispersed coins.** 18 of 29 coin-venues are negative,
   several catastrophically (−85 bps). Coin selection would have to be *right in advance*, and I
   have no out-of-sample evidence that it can be.
5. **5-min snapshots hide everything that matters.** Entries/exits are assumed at exact snapshot
   ticks. All intra-bar adverse movement, the actual moment of fill, and any short-lived
   divergence are invisible.
6. **Funding accrued continuously**, not at true 8 h settlement boundaries. Immaterial here
   (holds are sub-hour) but wrong in principle, and it would matter for any longer-hold variant.
7. **19 days, one market regime**, Jul 27 – Aug 15. ёрш tape is only **2 days** and 15 coins/venue.
8. **No leverage, margin, or liquidation modelling.** A 250 bps structural gap held under leverage
   (see `ONE_USDT` below) is a liquidation risk this analysis is blind to.
9. **Survivorship in the exit rule.** Trades still open at series end are discarded rather than
   marked to market, which drops the worst never-converging tails.
10. **Structural basis ≠ convergence opportunity.** 26/467 coins have a *persistent* offset ≥30 bps
    that never reverts to zero — `ONE_USDT` sits at a real, independently-confirmed ~250 bps gap
    (verified across two collectors and the tape, not a multiplier bug; lag-1 autocorr 0.991).
    Entering these on a `|d| ≥ K` rule is buying a permanent basis, not a convergence trade.
11. **Fees assumed at posted maker rates** (MEXC 1.0 bp, Gate 2.0 bp), no rebate, but also no
    slippage, no rejects, no post-only repricing cost, no funding-settlement timing risk.

### What would actually settle it

A small targeted collector — same shape as the ёрш L2 one — on **~10 candidate pairs collected on
BOTH venues simultaneously**: L2 top-5 + tape + funding, tick-level, 2–3 weeks. That is the only
way to get (a) true fill probability including queue, (b) leg-risk measurement, (c) depth-aware
sizing. Candidate selection should favour coins with a **small persistent offset** but
**high deviation autocorrelation**, and should exclude the 26 structural-basis coins outright.

---

## Data integrity

Read-only session. No writes, no schema changes, no service changes, no push.

| table | rows | status |
|---|---|---|
| `spread_observations` | 471,481 | **frozen, unchanged** (window still 2026-07-06 17:34 → 2026-07-27 13:06) |
| `funding_basis_snapshots` | 6,512,937 | intact (live collector, still ingesting) |
| `book_ticker` | 594,147 | intact (ёрш, live) |
| `tape_prints` | 1,016,547 | intact (ёрш, live) |
| `ersh_book_l2` | 2,459,270 | intact (ёрш, live) |

Row growth in the live tables is the running collectors, not this analysis.
