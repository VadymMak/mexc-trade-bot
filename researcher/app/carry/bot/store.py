"""Persistence for the carry bot — ADDITIVE, self-healing.

Two new tables, both CREATE TABLE IF NOT EXISTS, both touched by nothing else:
    paper_carry_positions — one row per LEG (spot and perp) of a paper position
    paper_carry_events    — the runner log: selections, accruals, risk triggers

Touches no existing table. The arb archive (ml_trade_outcomes, paper_positions,
spread_observations) and the ёрш tables are not read or written here. Note that
`paper_positions` is the OLD arbitrage table and is deliberately NOT reused —
it has a known schema bug and belongs to a dead strategy.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_carry_positions (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                TEXT,
    group_id              TEXT,              -- both legs of one carry share this
    opened_ts             TIMESTAMPTZ DEFAULT now(),
    exchange              TEXT,
    symbol                TEXT,
    leg                   TEXT,              -- 'spot' | 'perp'
    side                  TEXT,              -- 'long' | 'short'
    notional_usd          DOUBLE PRECISION,
    entry_price           DOUBLE PRECISION,
    leverage              DOUBLE PRECISION,
    status                TEXT,              -- 'open' | 'closed'
    closed_ts             TIMESTAMPTZ,
    close_price           DOUBLE PRECISION,
    realised_funding_usd  DOUBLE PRECISION DEFAULT 0,
    modelled_funding_usd  DOUBLE PRECISION DEFAULT 0,
    entry_cost_usd        DOUBLE PRECISION DEFAULT 0,
    exit_cost_usd         DOUBLE PRECISION DEFAULT 0,
    paper_pnl_usd         DOUBLE PRECISION DEFAULT 0,
    last_epoch            BIGINT,
    interval_hours        DOUBLE PRECISION,
    entry_depth_usd       DOUBLE PRECISION,  -- worst-hour capacity at entry (R5)
    depth_basis           TEXT,              -- 'worst-hour' | 'p10-limited'
    notes                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_pcp_status ON paper_carry_positions (status, exchange, symbol);
CREATE INDEX IF NOT EXISTS idx_pcp_group  ON paper_carry_positions (group_id);

CREATE TABLE IF NOT EXISTS paper_carry_events (
    id        BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ DEFAULT now(),
    run_id    TEXT,
    level     TEXT,      -- 'info' | 'warn' | 'risk'
    kind      TEXT,      -- 'select' | 'open' | 'accrue' | 'risk' | 'close' | 'health'
    exchange  TEXT,
    symbol    TEXT,
    message   TEXT,
    data      JSONB
);
CREATE INDEX IF NOT EXISTS idx_pce_ts ON paper_carry_events (ts DESC);
"""


class BotStore:
    def __init__(self, pool, run_id: str) -> None:
        self._pool = pool
        self.run_id = run_id

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA)
        logger.info("[carry/bot] schema ready (paper_carry_positions, paper_carry_events)")

    # ---- events -----------------------------------------------------------
    async def event(self, level: str, kind: str, message: str,
                    ex: str | None = None, sym: str | None = None,
                    data: dict | None = None) -> None:
        await self._pool.execute(
            """INSERT INTO paper_carry_events
                   (run_id, level, kind, exchange, symbol, message, data)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            self.run_id, level, kind, ex, sym, message,
            json.dumps(data or {}, default=str))
        log = logger.warning if level in ("warn", "risk") else logger.info
        tag = f" {ex}/{sym}" if sym else ""
        log("[carry/bot][%s]%s %s", kind, tag, message)

    # ---- positions --------------------------------------------------------
    async def open_leg(self, group_id: str, ex: str, sym: str, leg: str, side: str,
                       notional_usd: float, entry_price: float, leverage: float,
                       entry_cost_usd: float, interval_hours: float,
                       last_epoch: int, entry_depth_usd: float,
                       depth_basis: str, notes: str) -> int:
        row = await self._pool.fetchrow(
            """INSERT INTO paper_carry_positions
                 (run_id, group_id, exchange, symbol, leg, side, notional_usd,
                  entry_price, leverage, status, entry_cost_usd, interval_hours,
                  last_epoch, entry_depth_usd, depth_basis, notes,
                  paper_pnl_usd)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open',$10,$11,$12,$13,$14,$15,$16)
               RETURNING id""",
            self.run_id, group_id, ex, sym, leg, side, notional_usd, entry_price,
            leverage, entry_cost_usd, interval_hours, last_epoch, entry_depth_usd,
            depth_basis, notes, -entry_cost_usd)
        return row["id"]

    async def open_positions(self) -> list:
        return await self._pool.fetch(
            """SELECT * FROM paper_carry_positions
               WHERE status='open' AND run_id=$1 ORDER BY group_id, leg""",
            self.run_id)

    async def open_groups(self) -> list:
        """One row per open carry (the perp leg carries the funding)."""
        return await self._pool.fetch(
            """SELECT group_id, exchange, symbol,
                      max(interval_hours)                       AS interval_hours,
                      max(last_epoch)                           AS last_epoch,
                      max(notional_usd)                         AS notional_usd,
                      max(entry_depth_usd)                      AS entry_depth_usd,
                      min(opened_ts)                            AS opened_ts,
                      sum(realised_funding_usd)                 AS realised_funding,
                      sum(modelled_funding_usd)                 AS modelled_funding,
                      sum(paper_pnl_usd)                        AS pnl,
                      sum(entry_cost_usd)                       AS entry_cost,
                      max(entry_price) FILTER (WHERE leg='spot') AS spot_entry,
                      max(entry_price) FILTER (WHERE leg='perp') AS perp_entry,
                      max(leverage)                             AS leverage
               FROM paper_carry_positions
               WHERE status='open' AND run_id=$1
               GROUP BY group_id, exchange, symbol""",
            self.run_id)

    async def accrue(self, group_id: str, realised_usd: float,
                     modelled_usd: float, epoch: int) -> None:
        """Funding lands on the PERP leg — that is the leg that pays/receives."""
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET realised_funding_usd = realised_funding_usd + $2,
                   modelled_funding_usd = modelled_funding_usd + $3,
                   paper_pnl_usd        = paper_pnl_usd + $2,
                   last_epoch           = $4
               WHERE group_id=$1 AND leg='perp' AND status='open'""",
            group_id, realised_usd, modelled_usd, epoch)
        await self._pool.execute(
            """UPDATE paper_carry_positions SET last_epoch=$2
               WHERE group_id=$1 AND status='open'""", group_id, epoch)

    async def close_group(self, group_id: str, exit_cost_usd: float,
                          reason: str) -> None:
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET status='closed', closed_ts=now(),
                   exit_cost_usd=$2,
                   paper_pnl_usd = paper_pnl_usd - $2,
                   notes = coalesce(notes,'') || ' | closed: ' || $3
               WHERE group_id=$1 AND status='open'""",
            group_id, exit_cost_usd / 2.0, reason)

    # ---- reporting --------------------------------------------------------
    async def summary(self) -> dict:
        row = await self._pool.fetchrow(
            """SELECT count(DISTINCT group_id) FILTER (WHERE status='open')   AS open_groups,
                      count(DISTINCT group_id) FILTER (WHERE status='closed') AS closed_groups,
                      coalesce(sum(notional_usd) FILTER (WHERE status='open' AND leg='spot'),0) AS notional,
                      coalesce(sum(realised_funding_usd),0) AS realised,
                      coalesce(sum(modelled_funding_usd),0) AS modelled,
                      coalesce(sum(paper_pnl_usd),0)        AS pnl,
                      min(opened_ts)                        AS first_open
               FROM paper_carry_positions WHERE run_id=$1""", self.run_id)
        return dict(row) if row else {}
