"""
STABLE-PAIR LP (liquidity-provision) FEE-YIELD collector — ADDITIVE, READ-ONLY.

Public endpoint, no keys, no orders, no private call path in this package.

WHY IT EXISTS — candidate yield source #4, and the most ORTHOGONAL one
    Perp funding (#1), dated basis (#2) and stablecoin lending (#3) are all
    prices of the same underlying appetite: leverage and credit. When leverage
    demand collapses, all three compress together — which would make a
    "portfolio" of them one position in three costumes. LP fee yield is paid out
    of TRADING VOLUME. Its driver is turnover and volatility, not borrow demand.
    It is therefore the first candidate that could genuinely diversify the other
    three. Could. Establishing whether it actually does is a correlation
    question over a shared calendar, which is why this is a collector.

    Stable-STABLE pools specifically: when both legs are pegged to the same
    unit, impermanent loss is near zero, so the fee yield is close to the real
    return. That premise is load-bearing, and common.py checks it per row rather
    than assuming it.

THE SEPARATION THAT IS THE WHOLE POINT
    apy_base and apy_reward are stored in SEPARATE columns and are never summed
    anywhere in this package. apy_base is the swap-fee yield — traders paid it,
    it is structural. apy_reward is minted token incentives — a subsidy with an
    end date. A pool paying 2% in fees and 20% in farm tokens is a 2% edge with
    a 20% subsidy, not a 22% edge. Measured 2026-08-24: convex PMUSD-CRVUSD
    published apyBase 0.00 against apyReward 22.93.

WHAT IT DOES NOT TOUCH
    Writes `lp_snapshots` + `lp_collector_health` and NOTHING else. Two CREATE
    TABLE IF NOT EXISTS, four CREATE INDEX IF NOT EXISTS, two INSERT. No UPDATE,
    DELETE, DROP, TRUNCATE or ALTER anywhere in this package.
    lending_snapshots, basis_snapshots, funding_basis_snapshots,
    venue_funding_snapshots, bybit_funding_snapshots, carry_book_l2,
    paper_carry_*, disk_health and every ёрш table are neither read nor written.
    No bot can see this data, deliberately: there is no execution path for an
    LP position, and there should not be one until the series says something.

ANTI-ZOMBIE (the perp WS unit once stayed "active" for 3.4 days while dead)
    - per-endpoint isolation with typed FetchError; a dead feed degrades one
      source, never the cycle
    - per-source failure counters; a source that raises cannot stop the others
    - watchdog: SOFT stall rebuilds the HTTP session, HARD stall EXITS so
      systemd restarts a genuinely wedged process
    - a health heartbeat row per source per cycle, so "still active" and "still
      collecting" cannot diverge silently

Run:  cd researcher && .venv/bin/python -m app.lp.main
      cd researcher && .venv/bin/python -m app.lp.main --once
      cd researcher && .venv/bin/python -m app.lp.main --once --dry-run
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
from .common import COLUMNS, DEPEG_NOTE, MIN_TVL_USD, TOP_N

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lp")

CYCLE_SECONDS = int(os.getenv("LP_CYCLE_SECONDS", "1800"))
STALL_SOFT_SECS = float(os.getenv("LP_STALL_SOFT_SECS", str(3 * CYCLE_SECONDS)))
STALL_HARD_SECS = float(os.getenv("LP_STALL_HARD_SECS", str(6 * CYCLE_SECONDS)))
DSN = os.getenv("NEON_DATABASE_URL", "")

_TEXT = {"pool_id", "project", "chain", "symbol", "legs", "venue_kind",
         "apy_base_vs_volume", "peg_currency", "yield_bearing_legs",
         "unclassified_legs", "il_risk_llama", "pool_meta", "endpoint"}
_BOOL = {"same_peg", "has_yield_bearing_leg", "concentrated_liquidity",
         "is_wrapper", "tvl_implausible", "outlier"}
_INT = {"n_legs", "datapoints"}
_TS = {"observed_at"}
_JSONB = {"extra"}


def _ddl() -> str:
    cols = []
    for c in COLUMNS:
        t = ("TEXT" if c in _TEXT else "BOOLEAN" if c in _BOOL
             else "INTEGER" if c in _INT else "TIMESTAMPTZ" if c in _TS
             else "JSONB" if c in _JSONB else "DOUBLE PRECISION")
        cols.append(f"    {c:<24} {t}")
    return (
        "CREATE TABLE IF NOT EXISTS lp_snapshots (\n"
        "    id                       BIGSERIAL PRIMARY KEY,\n"
        "    ts                       TIMESTAMPTZ DEFAULT now(),\n"
        + ",\n".join(cols) + "\n);\n"
        "CREATE INDEX IF NOT EXISTS idx_lp_ts ON lp_snapshots (ts DESC);\n"
        "CREATE INDEX IF NOT EXISTS idx_lp_pool ON lp_snapshots (pool_id, ts DESC);\n"
        "CREATE INDEX IF NOT EXISTS idx_lp_proj ON lp_snapshots (project, chain, ts DESC);\n"
        # The comparable view is: same peg, no yield-bearing leg, a real DEX.
        # It is the query this dataset exists to answer, so it gets an index.
        "CREATE INDEX IF NOT EXISTS idx_lp_clean ON lp_snapshots "
        "(same_peg, has_yield_bearing_leg, is_wrapper, ts DESC);\n"
        "CREATE TABLE IF NOT EXISTS lp_collector_health (\n"
        "    id                    BIGSERIAL PRIMARY KEY,\n"
        "    ts                    TIMESTAMPTZ DEFAULT now(),\n"
        "    cycle                 BIGINT,\n"
        "    source                TEXT,\n"
        "    rows_written          INTEGER,\n"
        "    pools_seen            INTEGER,\n"
        "    stable_flagged        INTEGER,\n"
        "    lp_multi              INTEGER,\n"
        "    above_tvl_floor       INTEGER,\n"
        "    mixed_peg_fx          INTEGER,\n"
        "    yield_bearing         INTEGER,\n"
        "    wrapper               INTEGER,\n"
        "    tvl_implausible       INTEGER,\n"
        "    base_understated      INTEGER,\n"
        "    base_out_of_range     INTEGER,\n"
        "    no_apy                INTEGER,\n"
        "    dropped_beyond_top_n  INTEGER,\n"
        "    failed_endpoints      TEXT,\n"
        "    secs_since_last_write DOUBLE PRECISION,\n"
        "    consecutive_failures  INTEGER,\n"
        "    action                TEXT\n"
        ");\n"
        "CREATE INDEX IF NOT EXISTS idx_lph_ts ON lp_collector_health (ts DESC);\n"
        # The caveats belong to the DATASET, not to each row. Stated once here
        # so they travel with the table in the database itself — a reader who
        # finds lp_snapshots years from now with no access to this repo still
        # gets told how to read it. Previously these were repeated in every
        # row's extra JSONB, which made the prose ~75% of the stored bytes.
        + COMMENT_SQL
    )


COMMENT_SQL = (
    "COMMENT ON TABLE lp_snapshots IS "
    "'Stable-pair LP fee yields from the DefiLlama yields API. READ-ONLY public "
    "data, no keys, no orders. apy_base (swap fees paid by traders = the "
    "structural edge) and apy_reward (minted token incentives = a subsidy with "
    "an end date) are SEPARATE columns and must NEVER be summed; apy_total is "
    "stored only to reproduce the published headline. Rows carrying same_peg="
    "false, has_yield_bearing_leg, is_wrapper or tvl_implausible are recorded "
    "deliberately and are NOT comparable fee yields - the comparable set is "
    "same_peg AND NOT has_yield_bearing_leg AND NOT is_wrapper AND venue_kind="
    "''dex''. TAIL RISK: a stable LP''s real exposure is a DEPEG - if one leg "
    "leaves $1 the AMM sells the sound asset for the failing one, so the LP is "
    "left holding the broken leg. That is the LP analogue of adverse selection "
    "and no APR column prices it.';\n"
    "COMMENT ON COLUMN lp_snapshots.apy_base IS "
    "'REAL swap-fee yield, annual percent. The structural edge. Never add "
    "apy_reward to this.';\n"
    "COMMENT ON COLUMN lp_snapshots.apy_reward IS "
    "'Token incentives, annual percent. A temporary SUBSIDY, not an edge.';\n"
    "COMMENT ON COLUMN lp_snapshots.concentrated_liquidity IS "
    "'TRUE for Uniswap v3/v4 and every other range AMM: the published APR is "
    "POOL-LEVEL, not a personal-range yield. A real LP picks a range and earns "
    "more inside it and nothing outside it.';\n"
    "COMMENT ON COLUMN lp_snapshots.same_peg IS "
    "'TRISTATE. TRUE = all legs share a peg currency, so the near-zero-IL "
    "premise holds. FALSE = mixed currencies (e.g. USDC/EURC) carrying real FX "
    "risk - these sit at the TOP of the apy_base ranking and are the main way "
    "this dataset misleads. NULL = a leg could not be classified.';\n"
    "COMMENT ON COLUMN lp_snapshots.has_yield_bearing_leg IS "
    "'TRUE when a leg accrues native yield (sUSDe, sDAI, aTokens, ...), so "
    "apy_base is contaminated by yield that is not a swap fee and is already "
    "counted by the lending collector.';\n"
    "COMMENT ON COLUMN lp_snapshots.is_wrapper IS "
    "'TRUE for convex/stake-dao/yearn/beefy, which re-list another protocol''s "
    "pool: TVL double-counts the underlying DEX pool and apy_base may fold in "
    "compounded rewards.';\n"
    "COMMENT ON COLUMN lp_snapshots.apy_base_vs_volume IS "
    "'Cross-check of published apy_base against observed turnover. "
    "base_understated = apy_base is below what a 1bp fee on volumeUsd1d would "
    "already pay, so treat it as stale or rounded.';\n"
)


CREATE_SQL = _ddl()
INSERT_SQL = (f"INSERT INTO lp_snapshots ({', '.join(COLUMNS)}) VALUES ("
              + ",".join(f"${i}" for i in range(1, len(COLUMNS) + 1)) + ")")

_HEALTH_KEYS = ("cycle", "source", "rows_written", "pools_seen",
                "stable_flagged", "lp_multi", "above_tvl_floor", "mixed_peg_fx",
                "yield_bearing", "wrapper", "tvl_implausible",
                "base_understated", "base_out_of_range", "no_apy",
                "dropped_beyond_top_n", "failed_endpoints",
                "secs_since_last_write", "consecutive_failures", "action")


class Stalled(RuntimeError):
    """No source wrote for STALL_HARD_SECS — exit for a clean systemd restart."""


async def write_health(pool, **kw) -> None:
    """Best-effort: a health-write failure must never mask the real error."""
    try:
        await pool.execute(
            f"INSERT INTO lp_collector_health ({', '.join(_HEALTH_KEYS)}) VALUES ("
            + ",".join(f"${i}" for i in range(1, len(_HEALTH_KEYS) + 1)) + ")",
            *(kw.get(k) for k in _HEALTH_KEYS))
    except Exception as exc:                          # noqa: BLE001
        log.warning("[lp] health write failed: %r", exc)


async def run_source(ad, session, pool, t0: float, cycle: int, state: dict,
                     dry_run: bool = False) -> int:
    """One source's cycle. Never raises — a bad source must not stop the others."""
    if not ad.due(t0):
        return 0
    try:
        rows, stats, failures = await ad.snapshot(session)
        ad.mark(t0)
        n = 0
        if rows and not dry_run:
            async with pool.acquire() as con:
                await con.executemany(INSERT_SQL, [r.as_tuple() for r in rows])
            n = len(rows)
            state["last_write"] = t0
            state["fails"] = 0
        elif rows and dry_run:
            n = len(rows)
            state["last_write"] = t0
        else:
            state["fails"] += 1

        log.info("[lp/%s] %d rows written | universe: %d pools -> %d stable-"
                 "flagged -> %d LP(multi) -> %d above $%.0fk TVL | flags: "
                 "mixed-peg/FX=%d yield-bearing=%d wrapper=%d tvl-implausible=%d "
                 "base-understated=%d | rejected: out-of-range=%d no-apy=%d%s",
                 ad.name, n, stats.get("pools_seen", 0),
                 stats.get("stable_flagged", 0), stats.get("lp_multi", 0),
                 stats.get("above_tvl_floor", 0), MIN_TVL_USD / 1000.0,
                 stats.get("mixed_peg_fx", 0), stats.get("yield_bearing", 0),
                 stats.get("wrapper", 0), stats.get("tvl_implausible", 0),
                 stats.get("base_understated", 0),
                 stats.get("base_out_of_range", 0), stats.get("no_apy", 0),
                 ("  DEGRADED: " + ", ".join(f.url for f in failures))
                 if failures else "")
        if not dry_run:
            await write_health(
                pool, cycle=cycle, source=ad.name, rows_written=n,
                pools_seen=stats.get("pools_seen"),
                stable_flagged=stats.get("stable_flagged"),
                lp_multi=stats.get("lp_multi"),
                above_tvl_floor=stats.get("above_tvl_floor"),
                mixed_peg_fx=stats.get("mixed_peg_fx"),
                yield_bearing=stats.get("yield_bearing"),
                wrapper=stats.get("wrapper"),
                tvl_implausible=stats.get("tvl_implausible"),
                base_understated=stats.get("base_understated"),
                base_out_of_range=stats.get("base_out_of_range"),
                no_apy=stats.get("no_apy"),
                dropped_beyond_top_n=stats.get("dropped_beyond_top_n"),
                failed_endpoints=", ".join(f.url for f in failures) or None,
                secs_since_last_write=t0 - state["last_write"],
                consecutive_failures=state["fails"],
                action=("partial" if failures or not n else "ok"))
        return n
    except Exception as exc:                          # noqa: BLE001 — loud, counted
        state["fails"] += 1
        log.exception("[lp/%s] cycle %d FAILED (%d in a row): %r",
                      ad.name, cycle, state["fails"], exc)
        if not dry_run:
            await write_health(pool, cycle=cycle, source=ad.name, rows_written=0,
                               secs_since_last_write=t0 - state["last_write"],
                               consecutive_failures=state["fails"], action="error")
        return 0


async def run(once: bool = False, dry_run: bool = False) -> None:
    pool = None
    if not dry_run:
        if not DSN:
            raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")
        pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=4,
                                         command_timeout=60.0,
                                         statement_cache_size=0)
        async with pool.acquire() as con:
            await con.execute(CREATE_SQL)

    adapters = [A() for A in ADAPTERS]
    log.info("[lp] %s; sources=%s; cycle=%ds; watchdog soft=%.0fs hard=%.0fs | "
             "stable-STABLE LP pools, TVL >= $%.0f, top %d by TVL | "
             "PUBLIC READ-ONLY, no keys, no orders",
             "DRY RUN — nothing will be written" if dry_run
             else "connected; lp_snapshots ready",
             [a.name for a in adapters], CYCLE_SECONDS, STALL_SOFT_SECS,
             STALL_HARD_SECS, MIN_TVL_USD, TOP_N)
    # Stated at every startup so the caveats travel with the service, not just
    # with whoever happened to read the commit message.
    log.info("[lp] apy_base (swap fees, the structural edge) and apy_reward "
             "(token incentives, a subsidy that ends) are SEPARATE columns and "
             "are never summed. apy_total is stored only to reproduce the "
             "published headline.")
    log.info("[lp] CLMM pools (uniswap-v3/v4 and every other range AMM) are "
             "flagged concentrated_liquidity: the published APR is POOL-LEVEL, "
             "not a personal-range yield.")
    log.info("[lp] DEPEG: %s", DEPEG_NOTE)

    loop = asyncio.get_event_loop()
    session = aiohttp.ClientSession()
    state = {a.name: {"last_write": loop.time(), "fails": 0} for a in adapters}
    cycle = 0
    try:
        while True:
            cycle += 1
            t0 = loop.time()
            counts = await asyncio.gather(
                *(run_source(a, session, pool, t0, cycle, state[a.name], dry_run)
                  for a in adapters))
            log.info("[lp] cycle %d wrote %d rows across %d sources",
                     cycle, sum(counts), len(adapters))

            if once:
                return

            worst = max(t0 - state[a.name]["last_write"] for a in adapters)
            if worst > STALL_HARD_SECS:
                log.error("[lp] HARD STALL: a source has not written for %.0fs "
                          "(limit %.0fs) — EXITING for a clean restart",
                          worst, STALL_HARD_SECS)
                raise Stalled(f"no write for {worst:.0f}s")
            if worst > STALL_SOFT_SECS:
                log.error("[lp] SOFT STALL: %.0fs without a write (limit %.0fs) "
                          "— rebuilding HTTP session", worst, STALL_SOFT_SECS)
                try:
                    await session.close()
                except Exception as exc:              # noqa: BLE001
                    log.warning("[lp] session close failed: %r", exc)
                session = aiohttp.ClientSession()

            await asyncio.sleep(max(1.0, CYCLE_SECONDS - (loop.time() - t0)))
    finally:
        await session.close()
        if pool is not None:
            await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and classify but write NOTHING to the database")
    a = ap.parse_args()
    try:
        asyncio.run(run(once=a.once, dry_run=a.dry_run))
    except KeyboardInterrupt:
        log.info("[lp] stopped by user")
    except Stalled as exc:
        log.error("[lp] exiting on watchdog: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
