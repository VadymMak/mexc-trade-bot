"""
Neon PostgreSQL interface (asyncpg).

All tables are expected to exist already.
Schema (create manually or via migration):

    CREATE TABLE IF NOT EXISTS spread_ticks (
        id             BIGSERIAL PRIMARY KEY,
        symbol         TEXT      NOT NULL,
        exchange_long  TEXT      NOT NULL,
        exchange_short TEXT      NOT NULL,
        long_price     NUMERIC   NOT NULL,
        short_price    NUMERIC   NOT NULL,
        spread_pct     NUMERIC   NOT NULL,
        zscore         NUMERIC,
        ts_ms          BIGINT    NOT NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_spread_ticks_sym_ts ON spread_ticks (symbol, ts_ms DESC);
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.NEON_DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=10.0,
            statement_cache_size=0,  # required for Neon serverless / pgBouncer
        )
        logger.info("[DB] Connected to Neon PostgreSQL")
    return _pool


async def close_pool() -> None:
    """Drain and close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[DB] Pool closed")


async def insert_spread_tick(
    symbol: str,
    exchange_long: str,
    exchange_short: str,
    long_price: float,
    short_price: float,
    spread_pct: float,
    zscore: Optional[float],
    ts_ms: int,
) -> None:
    """Insert a single spread snapshot row."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO spread_ticks
            (symbol, exchange_long, exchange_short,
             long_price, short_price, spread_pct, zscore, ts_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        symbol, exchange_long, exchange_short,
        long_price, short_price, spread_pct, zscore, ts_ms,
    )


async def insert_spread_ticks_batch(rows: list[tuple]) -> None:
    """
    Bulk insert multiple spread rows for efficiency.
    Each row: (symbol, exchange_long, exchange_short,
               long_price, short_price, spread_pct, zscore, ts_ms)
    """
    if not rows:
        return
    pool = await get_pool()
    await pool.executemany(
        """
        INSERT INTO spread_ticks
            (symbol, exchange_long, exchange_short,
             long_price, short_price, spread_pct, zscore, ts_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        rows,
    )
