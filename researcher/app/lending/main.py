"""
STABLECOIN LENDING / BORROW rate collector — ADDITIVE, READ-ONLY, DATA ONLY.

Public endpoints, no keys, no orders, no private call path in this package.

WHY IT EXISTS
    Candidate yield source #3. Funding (perp) and dated basis are both prices of
    LEVERAGE demand; stablecoin lending is the price of CREDIT in the margin
    market. Whether the three move together or apart across regimes decides
    whether they are one edge in three costumes or an actual portfolio. That is
    a correlation question, so it needs a series, not a snapshot — hence a
    collector rather than a one-off read.

WHAT IT DOES NOT TOUCH
    Writes `lending_snapshots` + `lending_collector_health` and NOTHING else.
    funding_basis_snapshots, venue_funding_snapshots, bybit_funding_snapshots,
    basis_snapshots, basis_collector_health, carry_book_l2, paper_carry_*,
    disk_health and every ёрш table are neither read nor written. No bot can see
    this data, deliberately: there is no execution path for a lending position.

NORMALISATION. Five sources, four different rate clocks, none of them labelled
in the payload. Every row therefore carries raw_rate + raw_basis +
conversion_factor + rate_field so annual_pct is reproducible rather than
asserted. See the table in common.py for each convention and its corroboration.

ANTI-ZOMBIE (the perp WS unit once stayed "active" for 3.4 days while dead)
    - per-endpoint isolation with typed FetchError; one dead feed degrades one
      source, never the cycle
    - per-source failure counters; a source that raises cannot stop the others
    - watchdog: SOFT stall rebuilds the HTTP session, HARD stall EXITS so
      systemd restarts a genuinely wedged process
    - a health heartbeat row per source per cycle, so "still active" and "still
      collecting" cannot diverge silently

Run:  cd researcher && .venv/bin/python -m app.lending.main
      cd researcher && .venv/bin/python -m app.lending.main --once
      cd researcher && .venv/bin/python -m app.lending.main --once --source okx
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
from .common import AUTH_SKIPPED, COLUMNS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lending")

CYCLE_SECONDS = int(os.getenv("LENDING_CYCLE_SECONDS", "300"))
STALL_SOFT_SECS = float(os.getenv("LENDING_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("LENDING_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
DSN = os.getenv("NEON_DATABASE_URL", "")

_TEXT = {"source", "venue_kind", "asset", "rate_type", "raw_basis",
         "rate_field", "tier", "term", "endpoint"}
_TS = {"observed_at"}
_JSONB = {"extra"}


def _ddl() -> str:
    cols = []
    for c in COLUMNS:
        t = ("TEXT" if c in _TEXT else "TIMESTAMPTZ" if c in _TS
             else "JSONB" if c in _JSONB else "DOUBLE PRECISION")
        cols.append(f"    {c:<20} {t}")
    return (
        "CREATE TABLE IF NOT EXISTS lending_snapshots (\n"
        "    id                   BIGSERIAL PRIMARY KEY,\n"
        "    ts                   TIMESTAMPTZ DEFAULT now(),\n"
        + ",\n".join(cols) + "\n);\n"
        "CREATE INDEX IF NOT EXISTS idx_ls_ts ON lending_snapshots (ts DESC);\n"
        "CREATE INDEX IF NOT EXISTS idx_ls_key ON lending_snapshots "
        "(source, asset, rate_type, ts DESC);\n"
        "CREATE TABLE IF NOT EXISTS lending_collector_health (\n"
        "    id                    BIGSERIAL PRIMARY KEY,\n"
        "    ts                    TIMESTAMPTZ DEFAULT now(),\n"
        "    cycle                 BIGINT,\n"
        "    source                TEXT,\n"
        "    rows_written          INTEGER,\n"
        "    missing               INTEGER,\n"
        "    out_of_range          INTEGER,\n"
        "    no_conversion         INTEGER,\n"
        "    no_tier               INTEGER,\n"
        "    failed_endpoints      TEXT,\n"
        "    secs_since_last_write DOUBLE PRECISION,\n"
        "    consecutive_failures  INTEGER,\n"
        "    action                TEXT\n"
        ");\n"
        "CREATE INDEX IF NOT EXISTS idx_lch_ts ON lending_collector_health (ts DESC);\n"
    )


CREATE_SQL = _ddl()
INSERT_SQL = (f"INSERT INTO lending_snapshots ({', '.join(COLUMNS)}) VALUES ("
              + ",".join(f"${i}" for i in range(1, len(COLUMNS) + 1)) + ")")


class Stalled(RuntimeError):
    """No source wrote for STALL_HARD_SECS — exit for a clean systemd restart."""


async def write_health(pool, **kw) -> None:
    """Best-effort: a health-write failure must never mask the real error."""
    keys = ("cycle", "source", "rows_written", "missing", "out_of_range",
            "no_conversion", "no_tier", "failed_endpoints",
            "secs_since_last_write", "consecutive_failures", "action")
    try:
        await pool.execute(
            f"INSERT INTO lending_collector_health ({', '.join(keys)}) VALUES ("
            + ",".join(f"${i}" for i in range(1, len(keys) + 1)) + ")",
            *(kw.get(k) for k in keys))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[lending] health write failed: %r", exc)


async def run_source(ad, session, pool, t0: float, cycle: int, state: dict) -> int:
    """One source's cycle. Never raises — a bad source must not stop the others."""
    if not ad.due(t0):
        return 0
    try:
        rows, stats, failures = await ad.snapshot(session)
        ad.mark(t0)
        n = 0
        if rows:
            async with pool.acquire() as con:
                await con.executemany(INSERT_SQL, [r.as_tuple() for r in rows])
            n = len(rows)
            state["last_write"] = t0
            state["fails"] = 0
        else:
            state["fails"] += 1

        log.info("[lending/%s] %d rows (missing=%d, out-of-range=%d, "
                 "no-conversion=%d, no-tier=%d)%s", ad.name, n,
                 stats.get("missing", 0), stats.get("out_of_range", 0),
                 stats.get("no_conversion", 0), stats.get("no_tier", 0),
                 ("  DEGRADED: " + ", ".join(f.url for f in failures))
                 if failures else "")
        await write_health(
            pool, cycle=cycle, source=ad.name, rows_written=n,
            missing=stats.get("missing"), out_of_range=stats.get("out_of_range"),
            no_conversion=stats.get("no_conversion"), no_tier=stats.get("no_tier"),
            failed_endpoints=", ".join(f.url for f in failures) or None,
            secs_since_last_write=t0 - state["last_write"],
            consecutive_failures=state["fails"],
            action=("partial" if failures or not n else "ok"))
        return n
    except Exception as exc:                          # noqa: BLE001 — loud, counted
        state["fails"] += 1
        log.exception("[lending/%s] cycle %d FAILED (%d in a row): %r",
                      ad.name, cycle, state["fails"], exc)
        await write_health(pool, cycle=cycle, source=ad.name, rows_written=0,
                           secs_since_last_write=t0 - state["last_write"],
                           consecutive_failures=state["fails"], action="error")
        return 0


async def run(once: bool = False, only: str | None = None) -> None:
    if not DSN:
        raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=4,
                                     command_timeout=60.0, statement_cache_size=0)
    async with pool.acquire() as con:
        await con.execute(CREATE_SQL)

    adapters = [A() for A in ADAPTERS if only is None or A.name == only]
    if not adapters:
        raise SystemExit(f"unknown source {only!r}")
    log.info("[lending] connected; lending_snapshots ready; sources=%s; "
             "cycle=%ds; watchdog soft=%.0fs hard=%.0fs | USDT+USDC, "
             "supply+borrow | PUBLIC READ-ONLY, no keys, no orders",
             [a.name for a in adapters], CYCLE_SECONDS,
             STALL_SOFT_SECS, STALL_HARD_SECS)
    for who, why in AUTH_SKIPPED:
        log.info("[lending] NOT COLLECTED — %s: %s", who, why)

    loop = asyncio.get_event_loop()
    session = aiohttp.ClientSession()
    state = {a.name: {"last_write": loop.time(), "fails": 0} for a in adapters}
    cycle = 0
    try:
        while True:
            cycle += 1
            t0 = loop.time()
            counts = await asyncio.gather(
                *(run_source(a, session, pool, t0, cycle, state[a.name])
                  for a in adapters))
            log.info("[lending] cycle %d wrote %d rows across %d sources",
                     cycle, sum(counts), len(adapters))

            if once:
                return

            worst = max(t0 - state[a.name]["last_write"] for a in adapters)
            if worst > STALL_HARD_SECS:
                log.error("[lending] HARD STALL: a source has not written for "
                          "%.0fs (limit %.0fs) — EXITING for a clean restart",
                          worst, STALL_HARD_SECS)
                raise Stalled(f"no write for {worst:.0f}s")
            if worst > STALL_SOFT_SECS:
                log.error("[lending] SOFT STALL: %.0fs without a write (limit "
                          "%.0fs) — rebuilding HTTP session", worst, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[lending] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            await asyncio.sleep(max(1.0, CYCLE_SECONDS - (loop.time() - t0)))
    finally:
        await session.close()
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--source", default=None)
    a = ap.parse_args()
    try:
        asyncio.run(run(once=a.once, only=a.source))
    except KeyboardInterrupt:
        log.info("[lending] stopped by user")
    except Stalled as exc:
        log.error("[lending] exiting on watchdog: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
