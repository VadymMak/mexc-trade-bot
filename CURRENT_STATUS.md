# CURRENT STATUS — 2026-08-14 (authoritative; supersedes older overviews)

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
