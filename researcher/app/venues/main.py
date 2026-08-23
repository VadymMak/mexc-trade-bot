"""
Candidate-venue funding collector — OKX, Bitget, KuCoin, Bybit.

ADDITIVE, READ-ONLY, DATA COLLECTION ONLY. Public endpoints, no keys, no
orders, no private call path anywhere in this package.

PURPOSE: by 2026-08-26, an approximate cross-venue picture of WHICH exchange
actually carries the thin fat-funding alt tail — the breadth half of the EUR30k
question (skill trading-edge-lessons §17).

DELIBERATELY INVISIBLE TO THE BOT
    Writes ONE shared table `venue_funding_snapshots` (the Bybit collector's
    schema, which already carries an `exchange` column). It does NOT write
    funding_basis_snapshots: the paper bot's selector reads that table and
    would immediately try to "trade" venues we have no execution path for.
    Gate/MEXC stay there; the candidates stay here; the Aug 26 analysis UNIONs
    the two.

    The standalone mexc-bybit-funding service is left running and untouched —
    it keeps writing bybit_funding_snapshots. Bybit is ALSO collected here so
    all four candidates share one dataset. That costs 2 extra public calls per
    300s and is much cheaper than editing a collector that already works.

EACH VENUE IS ISOLATED FROM THE OTHERS. One venue erroring, timing out or
returning nonsense removes only its own rows for that cycle; the other three
still write. Within a venue, endpoints are isolated from each other too.

Run:  cd researcher && .venv/bin/python -m app.venues.main
      cd researcher && .venv/bin/python -m app.venues.main --once
      cd researcher && .venv/bin/python -m app.venues.main --once --venue okx
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import aiohttp
import asyncpg

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from .adapters import ADAPTERS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("venues")

CYCLE_SECONDS = int(os.getenv("VENUES_CYCLE_SECONDS", "300"))
STALL_SOFT_SECS = float(os.getenv("VENUES_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("VENUES_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
DSN = os.getenv("NEON_DATABASE_URL", "")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS venue_funding_snapshots (
    id                     BIGSERIAL PRIMARY KEY,
    ts                     TIMESTAMPTZ      DEFAULT now(),
    exchange               TEXT,
    symbol                 TEXT,
    perp_mark              DOUBLE PRECISION,
    spot_price             DOUBLE PRECISION,
    basis_bps              DOUBLE PRECISION,
    funding_rate           DOUBLE PRECISION,
    funding_interval_hours DOUBLE PRECISION,
    mins_to_funding        DOUBLE PRECISION,
    funding_annualized_pct DOUBLE PRECISION,
    perp_bid               DOUBLE PRECISION,
    perp_ask               DOUBLE PRECISION,
    perp_spread_bps        DOUBLE PRECISION,
    spot_bid               DOUBLE PRECISION,
    spot_ask               DOUBLE PRECISION,
    spot_spread_bps        DOUBLE PRECISION,
    perp_depth5_usd        DOUBLE PRECISION,
    spot_depth5_usd        DOUBLE PRECISION,
    next_settle_time       TIMESTAMPTZ,
    interval_source        TEXT,
    perp_volume24_base     DOUBLE PRECISION,
    perp_volume24_usd      DOUBLE PRECISION,
    perp_open_interest     DOUBLE PRECISION,
    spot_volume24_base     DOUBLE PRECISION,
    spot_volume24_usd      DOUBLE PRECISION,
    perp_bid_size          DOUBLE PRECISION,
    perp_ask_size          DOUBLE PRECISION,
    spot_bid_size          DOUBLE PRECISION,
    spot_ask_size          DOUBLE PRECISION,
    contract_multiplier    DOUBLE PRECISION,
    perp_index_price       DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_vfs_ts       ON venue_funding_snapshots (ts DESC);
CREATE INDEX IF NOT EXISTS idx_vfs_ex_sym   ON venue_funding_snapshots (exchange, symbol, ts DESC);

CREATE TABLE IF NOT EXISTS venue_collector_health (
    id                    BIGSERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ DEFAULT now(),
    cycle                 BIGINT,
    exchange              TEXT,
    rows_written          INTEGER,
    universe_size         INTEGER,
    unpriced              INTEGER,
    unit_skip             INTEGER,
    failed_endpoints      TEXT,
    secs_since_last_write DOUBLE PRECISION,
    consecutive_failures  INTEGER,
    action                TEXT
);
CREATE INDEX IF NOT EXISTS idx_vch_ts ON venue_collector_health (ts DESC);
"""

INSERT_SQL = """
INSERT INTO venue_funding_snapshots
    (exchange, symbol, perp_mark, spot_price, basis_bps, funding_rate,
     funding_interval_hours, mins_to_funding, funding_annualized_pct,
     perp_bid, perp_ask, perp_spread_bps, spot_bid, spot_ask, spot_spread_bps,
     perp_depth5_usd, spot_depth5_usd, next_settle_time, interval_source,
     perp_volume24_base, perp_volume24_usd, perp_open_interest,
     spot_volume24_base, spot_volume24_usd,
     perp_bid_size, perp_ask_size, spot_bid_size, spot_ask_size,
     contract_multiplier, perp_index_price)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
        $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30)
"""


class Stalled(RuntimeError):
    """No venue wrote anything for STALL_HARD_SECS — exit for a clean restart."""


async def write_health(pool, **kw) -> None:
    """Best-effort: a health-write failure must never mask the real error."""
    try:
        await pool.execute(
            """INSERT INTO venue_collector_health
                   (cycle, exchange, rows_written, universe_size, unpriced,
                    unit_skip, failed_endpoints, secs_since_last_write,
                    consecutive_failures, action)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            kw.get("cycle"), kw.get("exchange"), kw.get("rows_written"),
            kw.get("universe_size"), kw.get("unpriced"), kw.get("unit_skip"),
            kw.get("failed_endpoints"), kw.get("secs_since_last_write"),
            kw.get("consecutive_failures"), kw.get("action"))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[venues] health write failed: %r", exc)


async def run_venue(ad, session, pool, t0: float, cycle: int,
                    state: dict) -> int:
    """One venue's cycle. Never raises — a bad venue must not stop the others."""
    try:
        if ad.due(t0):
            await ad.refresh(session, t0)
        if not ad.universe:
            log.error("[venues/%s] no universe yet — nothing collected", ad.name)
            state["fails"] += 1
            return 0

        rows, stats, failures = await ad.snapshot(session)
        n = 0
        if rows:
            async with pool.acquire() as con:
                await con.executemany(INSERT_SQL, [r.as_tuple() for r in rows])
            n = len(rows)
            state["last_write"] = t0
            state["fails"] = 0
        else:
            state["fails"] += 1

        log.info("[venues/%s] %d rows (universe=%d, unpriced=%d, unit-skip=%d, "
                 "no-interval=%d)%s", ad.name, n, len(ad.universe),
                 stats.get("unpriced", 0), stats.get("unit_skip", 0),
                 stats.get("no_interval", 0),
                 ("  DEGRADED: " + ", ".join(f.url for f in failures)) if failures else "")
        await write_health(
            pool, cycle=cycle, exchange=ad.name, rows_written=n,
            universe_size=len(ad.universe), unpriced=stats.get("unpriced"),
            unit_skip=stats.get("unit_skip"),
            failed_endpoints=", ".join(f.url for f in failures) or None,
            secs_since_last_write=t0 - state["last_write"],
            consecutive_failures=state["fails"],
            action=("partial" if failures or not n else "ok"))
        return n
    except Exception as exc:                          # noqa: BLE001 — loud, counted
        state["fails"] += 1
        log.exception("[venues/%s] cycle %d FAILED (%d in a row): %r",
                      ad.name, cycle, state["fails"], exc)
        await write_health(pool, cycle=cycle, exchange=ad.name, rows_written=0,
                           universe_size=len(ad.universe),
                           secs_since_last_write=t0 - state["last_write"],
                           consecutive_failures=state["fails"], action="error")
        return 0


async def run(once: bool = False, only: str | None = None) -> None:
    if not DSN:
        raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=4,
                                     command_timeout=30.0, statement_cache_size=0)
    async with pool.acquire() as con:
        await con.execute(CREATE_SQL)

    adapters = [A() for A in ADAPTERS if only is None or A.name == only]
    if not adapters:
        raise SystemExit(f"unknown venue {only!r}")
    log.info("[venues] connected; venue_funding_snapshots ready; venues=%s; "
             "cycle=%ds; watchdog soft=%.0fs hard=%.0fs "
             "| PUBLIC READ-ONLY, no keys, no orders",
             [a.name for a in adapters], CYCLE_SECONDS,
             STALL_SOFT_SECS, STALL_HARD_SECS)

    loop = asyncio.get_event_loop()
    session = aiohttp.ClientSession()
    state = {a.name: {"last_write": loop.time(), "fails": 0} for a in adapters}
    cycle = 0
    try:
        while True:
            cycle += 1
            t0 = loop.time()
            # Venues run concurrently but are isolated: run_venue never raises.
            counts = await asyncio.gather(
                *(run_venue(a, session, pool, t0, cycle, state[a.name])
                  for a in adapters))
            total = sum(counts)
            log.info("[venues] cycle %d wrote %d rows across %d venues",
                     cycle, total, len(adapters))

            if once:
                return

            worst = max(t0 - state[a.name]["last_write"] for a in adapters)
            if worst > STALL_HARD_SECS:
                log.error("[venues] HARD STALL: a venue has not written for "
                          "%.0fs (limit %.0fs) — EXITING for a clean restart",
                          worst, STALL_HARD_SECS)
                raise Stalled(f"no write for {worst:.0f}s")
            if worst > STALL_SOFT_SECS:
                log.error("[venues] SOFT STALL: %.0fs without a write (limit "
                          "%.0fs) — rebuilding HTTP session", worst, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[venues] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            elapsed = loop.time() - t0
            await asyncio.sleep(max(1.0, CYCLE_SECONDS - elapsed))
    finally:
        await session.close()
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--venue", default=None)
    a = ap.parse_args()
    try:
        asyncio.run(run(once=a.once, only=a.venue))
    except KeyboardInterrupt:
        log.info("[venues] stopped by user")
    except Stalled as exc:
        log.error("[venues] exiting on watchdog: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
