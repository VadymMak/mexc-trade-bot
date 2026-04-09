"""
NeonDB — asyncpg wrapper for the researcher service.

Tables (auto-created on connect):
  paper_positions  — every paper trade open/close with full P&L
  pair_stats       — aggregated statistics per (symbol, ex_long, ex_short)
  spread_ticks     — raw spread snapshots for historical analysis

Usage:
    db = NeonDB(dsn)
    await db.connect()
    pos_id = await db.insert_paper_position(...)
    await db.close_paper_position(pos_id, ...)
    await db.upsert_pair_stats(symbol, ex_long, ex_short)
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


class NeonDB:
    def __init__(self, dsn: str) -> None:
        self._dsn  = dsn
        self._pool: Optional[asyncpg.Pool] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=5,
            command_timeout=10.0,
            statement_cache_size=0,   # required for Neon serverless / pgBouncer
        )
        logger.info("[DB] Connected to Neon PostgreSQL")
        await self._ensure_schema()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("[DB] Pool closed")

    # ── Schema bootstrap ──────────────────────────────────────────────────────

    async def _ensure_schema(self) -> None:
        assert self._pool
        await self._pool.execute("""
            -- Paper positions: one row per opened trade
            CREATE TABLE IF NOT EXISTS paper_positions (
                id                    BIGSERIAL PRIMARY KEY,
                symbol                TEXT         NOT NULL,
                exchange_long         TEXT         NOT NULL,
                exchange_short        TEXT         NOT NULL,
                entry_spread_pct      NUMERIC(10,4) NOT NULL,
                entry_zscore          NUMERIC(10,4),
                exit_spread_pct       NUMERIC(10,4),
                exit_zscore           NUMERIC(10,4),
                deal_size_usdt        NUMERIC(12,2) NOT NULL,
                slippage_entry_usdt   NUMERIC(10,6) NOT NULL DEFAULT 0,
                slippage_exit_usdt    NUMERIC(10,6) NOT NULL DEFAULT 0,
                fee_usdt              NUMERIC(10,6) NOT NULL DEFAULT 0,
                gross_pnl_usdt        NUMERIC(12,6),
                net_pnl_usdt          NUMERIC(12,6),
                hold_seconds          INT,
                status                TEXT         NOT NULL DEFAULT 'open',
                opened_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                closed_at             TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_pp_pair
                ON paper_positions(symbol, exchange_long, exchange_short);
            CREATE INDEX IF NOT EXISTS idx_pp_status
                ON paper_positions(status);
        """)
        # Migrate: add ALL columns that may be missing from older schema versions
        for col_ddl in [
            # Fix old column name (spread_pct_entry → entry_spread_pct):
            # set DEFAULT 0 so NOT NULL constraint doesn't crash on new INSERTs.
            # Wrapped in DO block — safe to run even if column doesn't exist.
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='paper_positions' AND column_name='spread_pct_entry'
                ) THEN
                    ALTER TABLE paper_positions ALTER COLUMN spread_pct_entry SET DEFAULT 0;
                END IF;
            END $$
            """,
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS entry_spread_pct    NUMERIC(10,4) NOT NULL DEFAULT 0",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS entry_zscore        NUMERIC(10,4)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS exit_spread_pct     NUMERIC(10,4)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS exit_zscore         NUMERIC(10,4)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS deal_size_usdt      NUMERIC(12,2) NOT NULL DEFAULT 0",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS slippage_entry_usdt NUMERIC(10,6) NOT NULL DEFAULT 0",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS slippage_exit_usdt  NUMERIC(10,6) NOT NULL DEFAULT 0",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS fee_usdt            NUMERIC(10,6) NOT NULL DEFAULT 0",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS gross_pnl_usdt      NUMERIC(12,6)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS net_pnl_usdt        NUMERIC(12,6)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS hold_seconds        INT",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS closed_at           TIMESTAMPTZ",
        ]:
            await self._pool.execute(col_ddl)
        await self._pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_pp_opened
                ON paper_positions(opened_at DESC);

            -- Aggregated stats per pair, recomputed after every close
            CREATE TABLE IF NOT EXISTS pair_stats (
                symbol            TEXT         NOT NULL,
                exchange_long     TEXT         NOT NULL,
                exchange_short    TEXT         NOT NULL,
                total_trades      INT          NOT NULL DEFAULT 0,
                win_trades        INT          NOT NULL DEFAULT 0,
                total_gross_pnl   NUMERIC(14,4) NOT NULL DEFAULT 0,
                total_net_pnl     NUMERIC(14,4) NOT NULL DEFAULT 0,
                total_fees        NUMERIC(14,4) NOT NULL DEFAULT 0,
                avg_hold_seconds  NUMERIC,
                sharpe            NUMERIC(8,4),
                max_drawdown_pct  NUMERIC(8,4),
                score             NUMERIC(6,2),
                promoted          BOOLEAN      NOT NULL DEFAULT FALSE,
                last_updated      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, exchange_long, exchange_short)
            );

            -- Raw spread ticks (rolling window, 30-day retention suggested)
            CREATE TABLE IF NOT EXISTS spread_ticks (
                id             BIGSERIAL    PRIMARY KEY,
                symbol         TEXT         NOT NULL,
                exchange_long  TEXT         NOT NULL,
                exchange_short TEXT         NOT NULL,
                spread_pct     NUMERIC      NOT NULL,
                zscore         NUMERIC,
                ts_ms          BIGINT       NOT NULL,
                created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_spread_ticks_sym_ts
                ON spread_ticks(symbol, ts_ms DESC);
        """)
        logger.info("[DB] Schema verified / created OK")

    # ── paper_positions ───────────────────────────────────────────────────────

    async def insert_paper_position(
        self,
        symbol:               str,
        exchange_long:        str,
        exchange_short:       str,
        entry_spread_pct:     float,
        entry_zscore:         Optional[float],
        deal_size_usdt:       float,
        slippage_entry_usdt:  float,
        fee_usdt:             float,
    ) -> int:
        """Insert open position; returns its auto-generated id."""
        assert self._pool
        row = await self._pool.fetchrow(
            """
            INSERT INTO paper_positions
                (symbol, exchange_long, exchange_short,
                 entry_spread_pct, entry_zscore,
                 deal_size_usdt, slippage_entry_usdt, fee_usdt)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            symbol, exchange_long, exchange_short,
            entry_spread_pct, entry_zscore,
            deal_size_usdt, slippage_entry_usdt, fee_usdt,
        )
        return int(row["id"])

    async def close_paper_position(
        self,
        pos_id:              int,
        exit_spread_pct:     float,
        exit_zscore:         Optional[float],
        slippage_exit_usdt:  float,
        gross_pnl_usdt:      float,
        net_pnl_usdt:        float,
        hold_seconds:        int,
    ) -> None:
        """Mark position as closed with full P&L."""
        assert self._pool
        await self._pool.execute(
            """
            UPDATE paper_positions SET
                status              = 'closed',
                exit_spread_pct     = $2,
                exit_zscore         = $3,
                slippage_exit_usdt  = $4,
                gross_pnl_usdt      = $5,
                net_pnl_usdt        = $6,
                hold_seconds        = $7,
                closed_at           = NOW()
            WHERE id = $1
            """,
            pos_id, exit_spread_pct, exit_zscore,
            slippage_exit_usdt, gross_pnl_usdt, net_pnl_usdt, hold_seconds,
        )

    # ── pair_stats ────────────────────────────────────────────────────────────

    async def upsert_pair_stats(
        self,
        symbol:        str,
        exchange_long: str,
        exchange_short: str,
    ) -> None:
        """
        Recompute pair_stats from all closed paper_positions for this pair.
        Calculates win rate, Sharpe ratio, max drawdown, composite score.
        """
        assert self._pool
        rows = await self._pool.fetch(
            """
            SELECT net_pnl_usdt, gross_pnl_usdt, fee_usdt,
                   hold_seconds, deal_size_usdt
            FROM paper_positions
            WHERE symbol=$1 AND exchange_long=$2 AND exchange_short=$3
              AND status='closed'
            ORDER BY closed_at ASC
            """,
            symbol, exchange_long, exchange_short,
        )
        if not rows:
            return

        n           = len(rows)
        win         = sum(1 for r in rows if (r["net_pnl_usdt"] or 0) > 0)
        total_gross = sum(float(r["gross_pnl_usdt"] or 0) for r in rows)
        total_net   = sum(float(r["net_pnl_usdt"]   or 0) for r in rows)
        total_fees  = sum(float(r["fee_usdt"]        or 0) for r in rows)
        holds       = [r["hold_seconds"] for r in rows if r["hold_seconds"]]
        avg_hold    = sum(holds) / len(holds) if holds else None

        # Net PnL % per trade for Sharpe
        net_pcts = [
            float(r["net_pnl_usdt"] or 0) / max(float(r["deal_size_usdt"] or 1), 0.01) * 100
            for r in rows
        ]
        avg_hold_min = (avg_hold / 60.0) if avg_hold else 15.0
        sharpe = _calc_sharpe(net_pcts, avg_hold_min)

        # Max drawdown from cumulative net PnL
        cum     = []
        running = 0.0
        for r in rows:
            running += float(r["net_pnl_usdt"] or 0)
            cum.append(running)
        max_dd = _calc_max_drawdown(cum)

        # Composite score 0–100
        win_rate     = win / n if n else 0.0
        sharpe_norm  = min(max(sharpe or 0.0, 0.0), 5.0) / 5.0   # 0→1
        trades_norm  = min(n / 200.0, 1.0)                        # 200 trades = full
        score        = win_rate * 40.0 + sharpe_norm * 40.0 + trades_norm * 20.0

        await self._pool.execute(
            """
            INSERT INTO pair_stats
                (symbol, exchange_long, exchange_short,
                 total_trades, win_trades,
                 total_gross_pnl, total_net_pnl, total_fees,
                 avg_hold_seconds, sharpe, max_drawdown_pct, score,
                 last_updated)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12, NOW())
            ON CONFLICT (symbol, exchange_long, exchange_short) DO UPDATE SET
                total_trades     = EXCLUDED.total_trades,
                win_trades       = EXCLUDED.win_trades,
                total_gross_pnl  = EXCLUDED.total_gross_pnl,
                total_net_pnl    = EXCLUDED.total_net_pnl,
                total_fees       = EXCLUDED.total_fees,
                avg_hold_seconds = EXCLUDED.avg_hold_seconds,
                sharpe           = EXCLUDED.sharpe,
                max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                score            = EXCLUDED.score,
                last_updated     = NOW()
            """,
            symbol, exchange_long, exchange_short,
            n, win,
            total_gross, total_net, total_fees,
            avg_hold, sharpe, max_dd * 100.0, score,
        )
        logger.debug(
            "[Stats] %s %s/%s trades=%d win_rate=%.1f%% sharpe=%.2f dd=%.1f%% score=%.1f",
            symbol, exchange_long, exchange_short,
            n, win_rate * 100, sharpe or 0.0, max_dd * 100.0, score,
        )

    async def get_queue_candidates(
        self,
        min_score:    float = 75.0,
        min_trades:   int   = 50,
        min_win_rate: float = 0.58,
    ) -> list[dict]:
        """Return pairs that meet promotion criteria."""
        assert self._pool
        rows = await self._pool.fetch(
            """
            SELECT symbol, exchange_long, exchange_short,
                   total_trades, win_trades,
                   total_net_pnl, sharpe, max_drawdown_pct, score
            FROM pair_stats
            WHERE score       >= $1
              AND total_trades >= $2
              AND promoted     = FALSE
              AND (win_trades::float / NULLIF(total_trades, 0)) >= $3
            ORDER BY score DESC
            LIMIT 20
            """,
            min_score, min_trades, min_win_rate,
        )
        return [dict(r) for r in rows]

    async def mark_promoted(
        self,
        symbol:        str,
        exchange_long: str,
        exchange_short: str,
    ) -> None:
        assert self._pool
        await self._pool.execute(
            """
            UPDATE pair_stats SET promoted=TRUE, last_updated=NOW()
            WHERE symbol=$1 AND exchange_long=$2 AND exchange_short=$3
            """,
            symbol, exchange_long, exchange_short,
        )

    # ── spread_ticks ──────────────────────────────────────────────────────────

    async def insert_spread_tick(
        self,
        symbol:        str,
        exchange_long: str,
        exchange_short: str,
        spread_pct:    float,
        zscore:        Optional[float],
        ts_ms:         int,
    ) -> None:
        if not self._pool:
            return
        await self._pool.execute(
            """
            INSERT INTO spread_ticks
                (symbol, exchange_long, exchange_short, spread_pct, zscore, ts_ms)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            symbol, exchange_long, exchange_short, spread_pct, zscore, ts_ms,
        )


# ── Module-level stat helpers (pure functions, reusable) ──────────────────────

def _calc_sharpe(net_pcts: list[float], avg_hold_min: float = 15.0) -> Optional[float]:
    n = len(net_pcts)
    if n < 5:
        return None
    mean     = sum(net_pcts) / n
    variance = sum((x - mean) ** 2 for x in net_pcts) / n
    std      = variance ** 0.5
    if std < 1e-9:
        return None
    trades_per_year = 365 * 24 * 60 / avg_hold_min
    return mean / std * math.sqrt(trades_per_year)


def _calc_max_drawdown(cum: list[float]) -> float:
    if not cum:
        return 0.0
    peak   = cum[0]
    max_dd = 0.0
    for v in cum:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd
