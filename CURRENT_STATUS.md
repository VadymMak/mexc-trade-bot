# CURRENT STATUS — 2026-08-14 (authoritative; supersedes older overviews)

> Historical as of 2026-08-26 — superseded by BLUEPRINT.md.

Supersedes the economics and strategy claims in `PROJECT_STATUS.md` and
`SERVER_STATUS_REPORT.md`. Where they disagree with this file, this file is right.

## Deployment
Home Ubuntu server `trading-server` (Tailscale 100.112.227.114), systemd, no Docker/nginx/Railway.
Dead artifacts: `backend/docker-compose.yml`, `backend/Dockerfile`, `ecosystem.config.js`,
`researcher/Procfile`, `railpack-plan.json`.

## Services (live, verified 2026-08-14)
Active: `mexc-backend` (127.0.0.1:8000 — loopback only, private), `mexc-frontend`
(0.0.0.0:3000 — reachable over Tailscale, NOT loopback-only), `mexc-carry-collector`,
`mexc-ersh-tape`, `mexc-ersh-l2`.
Paused: `mexc-researcher` (arbitrage) — inactive *and* disabled, so it stays down across reboots.
Database: local PostgreSQL `trading_bot`, role `mexc`.

## Strategy status — READ THIS, it corrects old docs

- **ARBITRAGE = DEAD (proven).** Mark-price sim showed 95% win / +22 bps; honest executable
  bid/ask + depth = 0.3% win / −214 bps (875 trades). Taker crossing cost *is* the whole loss.
  DO NOT resurrect. Old "5–10% daily risk-free" claims are FANTASY. `mexc-researcher` is paused;
  its data is a frozen archive.

- **FUNDING CARRY = REAL but MODEST.** ~5–10% APR on clean coins (BTC ~6–7%, QQQX ~9.6%);
  high-APR names are traps (unstable funding, wide spreads, broken basis). Collector runs in
  the background.
  *Caveat:* those APR figures come from the 2026-08-13 analysis over ~1.44M snapshots. The table
  now holds ~6.21M and the analysis has NOT been re-run against the larger set — treat the
  numbers as indicative, not current.

- **ЁРШ (maker spread-collection) = CURRENT ACTIVE RESEARCH.** The naive "farm dumb MMs" premise
  mostly FAILED: mid-markout is positive on *every* candidate = adverse selection everywhere, and
  the huge quoted spreads are FAKE (empty books, 18–178 ticks wide, ~$70/min of flow — you get
  undercut by one tick and there is no size to collect anyway). Narrowed to 5 LOCKED-1-TICK
  candidates where the spread equals one tick, so nobody can queue ahead of you by improving
  price: **gate LA_USDT (best), gate ONE_USDT, gate MYX_USDT, gate BMT_USDT, mexc ONE_USDT**.
  Now collecting L2 touch depth (`ersh_book_l2`). **NEXT = queue-aware maker-fill simulator**
  (fill only when the tape prints through our level AND we were at the front of the queue given
  touch size). Honest expectation: this may be a latency/queue game we cannot win from a
  non-colocated home server — the sim exists to find that out, not to confirm a hope.

## Data (live counts, 2026-08-14)
`funding_basis_snapshots` ~6.21M · `tape_prints` ~577k · `ersh_book_l2` ~18k (started today,
~2.0M rows/day ≈ 0.33 GB/day).
Arb archive (frozen): `ml_trade_outcomes` 1,888 · `paper_positions` 1,900 · `spread_observations`
~471k.

## Technical gotchas (don't repeat)
- MEXC depth needs `sub.depth.full` — plain `sub.depth` ignores `limit` and streams unsorted
  incremental diffs. Reading `levels[0]` from those as the touch made ONE_USDT look like a 444 bps
  market when the real spread was 15 bps. `app/core/market_flow.py` still has the wrong one, so the
  OLD arb MEXC `book_imbalance` was computed from diffs (moot; arb dead — but do not copy that call).
- **Markout must be measured from the book MID, not the trade price.** Trade-price markout fakes
  mean-reversion via the bid-ask bounce (a buy prints at the ask and reverts toward mid, ≈ −spread/2
  on *both* sides). Measured from mid, the sign flipped on all 27 candidates.
- **"Locked-1-tick" is TIME-VARYING** — mexc ONE_USDT widened ~6× (15 bps median → ~90–110 bps)
  within a day. Sims must condition on the prevailing spread regime, not assume 1 tick.
- Trade `size` is CONTRACTS on both venues; USD needs the contract multiplier
  (Gate `quanto_multiplier` / MEXC `contractSize`). `price × size` is meaningless.
- `/api/carry/export-dataset` is a MEMORY BOMB (`fetchall()` over millions of rows) — don't use it,
  export via targeted `psql`.
- Run server-touching work only in the SSH-connected Claude panel; never blind `git pull`.

## Housekeeping / SKIP
- `main` is **7 commits ahead of origin** (unpushed); the Brain code-index is stale (last indexed
  2026-07-06) — a push triggers the GitHub webhook and refreshes it.
- SKIP old arb threads: big-divergence (>5%) analysis, `paper_positions.features_complete` bug —
  both belong to a dead strategy.
- Lessons: Brain skill `trading-edge-lessons`. (Note: `LESSONS_LEARNED.md` is referenced by older
  notes but was never actually created in this repo — the skill is the only copy.)

## COLLECTOR CHANGE LOG (read this before any time-series analysis)

### 2026-08-19T12:59:25Z — `mexc-carry-collector` restarted (single change, single restart)

`researcher/app/carry/main.py`. Any analysis spanning this timestamp sees a
semantics change mid-series. **The ~1 cycle gap at this timestamp is a restart,
NOT a delisting wave** — the lifecycle classifier must exclude it.

**Fixed:** `funding_interval_hours` is now MEASURED per symbol (Gate
`funding_interval` / MEXC `collectCycle`, bulk endpoints, refreshed every 6h)
instead of hardcoded to 8. `mins_to_funding` now comes from the venue's own
`next_settle_time` instead of an assumed 00/08/16 UTC grid.
`funding_annualized_pct` uses the real interval. A symbol whose interval cannot
be resolved gets NULL in all three — never a default.

**Inputs stored beside outputs** (this is what prevents a repeat): `next_settle_time`
and `interval_source` are persisted on the row, so every derived value is now
reproducible from the row itself rather than from a constant living in code.

**Added (all NULLABLE, no DEFAULT => metadata-only ALTER, no table rewrite):**
`next_settle_time`, `interval_source`, `perp_volume24_base`, `perp_volume24_usd`,
`perp_open_interest`, `spot_volume24_base`, `spot_volume24_usd`, `perp_bid_size`,
`perp_ask_size`, `spot_bid_size`, `spot_ask_size`, `contract_multiplier`.
Historical rows keep NULL, which correctly reads as "not collected then".

**History was NOT rewritten.** View `v_carry_corrected` corrects at read time and
INNER JOINs `carry_funding_intervals`, so uncorrectable symbols drop out rather
than silently annualising at 8h. The collector now back-fills that cache for the
whole universe (129 -> 2,067 rows).

**Unchanged and verified unchanged:** universe logic, cycle cadence (300s), symbol
normalisation. Post-restart row rate is 1,197/cycle (mexc 672 + gate 525),
identical to the pre-restart baseline. The lifecycle series (presence_ratio
exactly 1.000 for every symbol) is intact.

**Caveats:** `funding_interval_hours` remains INTEGER, so a sub-hour interval
would round (none observed; all venues report 1/4/8/24). MEXC perp exposes no L1
sizes and Gate spot exposes no L1 sizes, so `perp_*_size` is Gate-only (43.9%)
and `spot_*_size` is MEXC-only (56.1%) — venue limitation, not a bug.

### 2026-08-19T13:28:12Z — `mexc-carry-depth` restarted (prompt-56 §5, depth)

`researcher/app/carry/depth_symbols.py`: CARRY_BASKET **129 → 153**. Added the 24
corrected-carry survivors that were not already covered (21 of the 45 already were).
Same 120s / 50-level config as the existing basket — homogeneous rows, so an analysis
can filter `level<=5` and get identical semantics across all 45 survivors. No change to
decode logic, cadence, universe logic or normalisation. Measured cost: +2.03M rows/day,
+278 MB/day.

### 2026-08-19T13:30:08Z — `mexc-carry-tape` STARTED (prompt-56 §5, tape) — TIME-BOXED

**NEW UNIT** `/etc/systemd/system/mexc-carry-tape.service`. Collects `tape_prints` +
`book_ticker` for 42 carry survivors so the authenticity screen can be run on them.

**STOP DATE 2026-08-22T13:30Z**, enforced by `RuntimeMaxSec=3d` in the unit — it stops
itself. The screen needs ~1-3 days of prints, not a permanent stream.

Runs as a SEPARATE PROCESS from `mexc-ersh-tape` via `ERSH_SYMBOL_SET=carry`
(`app/ersh/main.py`, default `ersh` = unchanged behaviour). **`mexc-ersh-tape` was NOT
restarted and NOT touched** — verified still up since 2026-08-13, NRestarts=0. Its 5-name
ёрш L2 stream and 30-name tape series are intact.

3 survivors (mexc BLUAI, mexc LAB, gate ONE) are EXCLUDED from the carry tape set because
the ёрш unit already collects them — double-collection would duplicate every print in
`tape_prints` and corrupt observed volume for exactly those names. 45 survivors − 3 = 42.

Measured cost: +0.96M rows/day, +162 MB/day, for 3 days only.

**Combined §5 cost: +440 MB/day during the tape window, +278 MB/day after. Current DB
growth ~1.9 GB/day → ~2.34 GB/day peak. 754 GB free.**

### STANDING WATCH — new-listing test (added 2026-08-19, prompt-57a)

**Do not re-conclude that the listing-obligation hypothesis was refuted.** A previous
session recorded it as refuted on the basis that FRONG's tape "starts 10 days after it
listed". That was an artifact: all 30 ёрш symbols' first prints fall inside a 273.5-second
window on 2026-08-13, i.e. when the collector started. **The hypothesis is UNTESTED.**

It can never be tested on FRONG or on anything listed before 2026-08-19 — tape began
2026-08-13, reported-volume persistence began 2026-08-19T12:59:25Z, and there is NO backfill.

It IS testable prospectively: any perp listing after 2026-08-19T12:59:25Z arrives with full
volume coverage from its first cycle. Standing query (read-only, no service):

    psql "$DSN" -f research/queries/new_listing_watch.sql

It reports, per new listing: whether reported volume is pinned in the $95k-$110k MEXC floor
band, the max/min hourly-volume ratio (a constant-rate emitter is ~1; organic flow ran ~2.9),
and whether both hold in the symbol's FIRST 24 hours or only later. Zero rows as of
2026-08-19T18:50Z; MEXC listed ~19 perps in the previous 23 days, so expect a first hit
within days.
