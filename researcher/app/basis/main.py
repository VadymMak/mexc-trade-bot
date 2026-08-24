"""
DATED-FUTURES BASIS collector — ADDITIVE, READ-ONLY, DATA COLLECTION ONLY.

Public endpoints, no keys, no orders, no private call path in this package.

WHY IT EXISTS
    Candidate edge #2 for the strategy portfolio: cash-and-carry. Buy spot,
    sell a DATED future trading at a premium, hold to expiry where the premium
    converges to zero by construction. Direction-neutral, and unlike perp
    funding the convergence horizon is FIXED rather than re-priced every epoch.

    Honest prior: basis and funding are both prices of leverage demand, so in
    this regime basis may well be as thin as funding is (~95% of the gate perp
    universe sits at the venue default). Collecting is how we find out rather
    than guess.

WHAT IT DOES NOT TOUCH
    Writes `basis_snapshots` + `basis_collector_health` and NOTHING else.
    funding_basis_snapshots, venue_funding_snapshots, bybit_funding_snapshots,
    carry_book_l2, paper_carry_* and every ёрш table are neither read nor
    written. The paper bot cannot see this data, deliberately: there is no
    execution path for dated futures.

ANTI-ZOMBIE (the perp WS unit stayed "active" for 3.4 days while dead)
    - per-endpoint isolation with typed FetchError; one dead feed degrades one
      venue, not the cycle
    - per-venue failure counters; a venue that raises never stops the others
    - watchdog: SOFT stall rebuilds the HTTP session, HARD stall EXITS so
      systemd restarts a genuinely wedged process
    - a health heartbeat row per venue per cycle, so "still active" and
      "still collecting" cannot diverge silently

Run:  cd researcher && .venv/bin/python -m app.basis.main
      cd researcher && .venv/bin/python -m app.basis.main --once
      cd researcher && .venv/bin/python -m app.basis.main --once --venue okx
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
from .common import COLUMNS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("basis")

CYCLE_SECONDS = int(os.getenv("BASIS_CYCLE_SECONDS", "300"))
STALL_SOFT_SECS = float(os.getenv("BASIS_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("BASIS_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
DSN = os.getenv("NEON_DATABASE_URL", "")

# Column types live beside COLUMNS so the DDL cannot drift from the INSERT.
_TEXT = {"exchange", "coin", "future_symbol", "spot_symbol", "contract_type",
         "settle_ccy", "cycle_label", "expiry_source", "spot_source",
         "spot_px_source", "future_px_source", "venue_basis_field"}
_TS = {"expiry_ts"}


def _ddl() -> str:
    cols = []
    for c in COLUMNS:
        t = "TEXT" if c in _TEXT else "TIMESTAMPTZ" if c in _TS else "DOUBLE PRECISION"
        cols.append(f"    {c:<22} {t}")
    return (
        "CREATE TABLE IF NOT EXISTS basis_snapshots (\n"
        "    id                     BIGSERIAL PRIMARY KEY,\n"
        "    ts                     TIMESTAMPTZ DEFAULT now(),\n"
        + ",\n".join(cols) + "\n);\n"
        "CREATE INDEX IF NOT EXISTS idx_bs_ts ON basis_snapshots (ts DESC);\n"
        "CREATE INDEX IF NOT EXISTS idx_bs_key "
        "ON basis_snapshots (exchange, coin, expiry_ts, ts DESC);\n"
        "CREATE TABLE IF NOT EXISTS basis_collector_health (\n"
        "    id                    BIGSERIAL PRIMARY KEY,\n"
        "    ts                    TIMESTAMPTZ DEFAULT now(),\n"
        "    cycle                 BIGINT,\n"
        "    exchange              TEXT,\n"
        "    rows_written          INTEGER,\n"
        "    universe_size         INTEGER,\n"
        "    unpriced              INTEGER,\n"
        "    no_spot               INTEGER,\n"
        "    no_expiry             INTEGER,\n"
        "    expired               INTEGER,\n"
        "    too_far               INTEGER,\n"
        "    unit_skip             INTEGER,\n"
        "    no_annual             INTEGER,\n"
        "    failed_endpoints      TEXT,\n"
        "    secs_since_last_write DOUBLE PRECISION,\n"
        "    consecutive_failures  INTEGER,\n"
        "    action                TEXT\n"
        ");\n"
        "CREATE INDEX IF NOT EXISTS idx_bch_ts ON basis_collector_health (ts DESC);\n"
    )


CREATE_SQL = _ddl()
INSERT_SQL = (f"INSERT INTO basis_snapshots ({', '.join(COLUMNS)}) VALUES ("
              + ",".join(f"${i}" for i in range(1, len(COLUMNS) + 1)) + ")")


class Stalled(RuntimeError):
    """No venue wrote for STALL_HARD_SECS — exit for a clean systemd restart."""


async def write_health(pool, **kw) -> None:
    """Best-effort: a health-write failure must never mask the real error."""
    keys = ("cycle", "exchange", "rows_written", "universe_size", "unpriced",
            "no_spot", "no_expiry", "expired", "too_far", "unit_skip",
            "no_annual", "failed_endpoints", "secs_since_last_write",
            "consecutive_failures", "action")
    try:
        await pool.execute(
            f"INSERT INTO basis_collector_health ({', '.join(keys)}) VALUES ("
            + ",".join(f"${i}" for i in range(1, len(keys) + 1)) + ")",
            *(kw.get(k) for k in keys))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[basis] health write failed: %r", exc)


async def run_venue(ad, session, pool, t0: float, cycle: int, state: dict) -> int:
    """One venue's cycle. Never raises — a bad venue must not stop the others."""
    try:
        if ad.due(t0):
            await ad.refresh(session, t0)
        if not ad.universe:
            log.error("[basis/%s] no dated-futures universe — nothing collected",
                      ad.name)
            state["fails"] += 1
            await write_health(pool, cycle=cycle, exchange=ad.name, rows_written=0,
                               universe_size=0,
                               secs_since_last_write=t0 - state["last_write"],
                               consecutive_failures=state["fails"],
                               action="no-universe")
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

        log.info("[basis/%s] %d rows (universe=%d, unpriced=%d, no-spot=%d, "
                 "expired=%d, too-far=%d, unit-skip=%d, no-annual=%d)%s",
                 ad.name, n, len(ad.universe), stats.get("unpriced", 0),
                 stats.get("no_spot", 0), stats.get("expired", 0),
                 stats.get("too_far", 0), stats.get("unit_skip", 0),
                 stats.get("no_annual", 0),
                 ("  DEGRADED: " + ", ".join(f.url for f in failures))
                 if failures else "")
        await write_health(
            pool, cycle=cycle, exchange=ad.name, rows_written=n,
            universe_size=len(ad.universe), unpriced=stats.get("unpriced"),
            no_spot=stats.get("no_spot"), no_expiry=stats.get("no_expiry"),
            expired=stats.get("expired"), too_far=stats.get("too_far"),
            unit_skip=stats.get("unit_skip"), no_annual=stats.get("no_annual"),
            failed_endpoints=", ".join(f.url for f in failures) or None,
            secs_since_last_write=t0 - state["last_write"],
            consecutive_failures=state["fails"],
            action=("partial" if failures or not n else "ok"))
        return n
    except Exception as exc:                          # noqa: BLE001 — loud, counted
        state["fails"] += 1
        log.exception("[basis/%s] cycle %d FAILED (%d in a row): %r",
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
    log.info("[basis] connected; basis_snapshots ready; venues=%s; cycle=%ds; "
             "watchdog soft=%.0fs hard=%.0fs | DATED FUTURES ONLY (no perps) "
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
            counts = await asyncio.gather(
                *(run_venue(a, session, pool, t0, cycle, state[a.name])
                  for a in adapters))
            log.info("[basis] cycle %d wrote %d rows across %d venues",
                     cycle, sum(counts), len(adapters))

            if once:
                return

            worst = max(t0 - state[a.name]["last_write"] for a in adapters)
            if worst > STALL_HARD_SECS:
                log.error("[basis] HARD STALL: a venue has not written for %.0fs "
                          "(limit %.0fs) — EXITING for a clean restart",
                          worst, STALL_HARD_SECS)
                raise Stalled(f"no write for {worst:.0f}s")
            if worst > STALL_SOFT_SECS:
                log.error("[basis] SOFT STALL: %.0fs without a write (limit %.0fs)"
                          " — rebuilding HTTP session", worst, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[basis] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            await asyncio.sleep(max(1.0, CYCLE_SECONDS - (loop.time() - t0)))
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
        log.info("[basis] stopped by user")
    except Stalled as exc:
        log.error("[basis] exiting on watchdog: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
