from __future__ import annotations

import datetime


class NeonDB:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool = None

    async def connect(self) -> None:
        import asyncpg
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=5,
            statement_cache_size=0,  # required for Neon serverless / pgBouncer
        )
        await self._create_tables()

    async def _create_tables(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange_long TEXT NOT NULL,
                    exchange_short TEXT NOT NULL,
                    spread_pct_entry FLOAT NOT NULL,
                    size_usdt FLOAT NOT NULL DEFAULT 10.0,
                    fee_usdt FLOAT NOT NULL DEFAULT 0.0,
                    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    closed_at TIMESTAMPTZ,
                    hold_minutes FLOAT,
                    gross_pnl_usdt FLOAT,
                    net_pnl_usdt FLOAT,
                    status TEXT NOT NULL DEFAULT 'open'
                );

                CREATE TABLE IF NOT EXISTS pair_stats (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange_long TEXT NOT NULL,
                    exchange_short TEXT NOT NULL,
                    total_trades INT NOT NULL DEFAULT 0,
                    win_count INT NOT NULL DEFAULT 0,
                    avg_hold_min FLOAT,
                    avg_net_pnl FLOAT,
                    signals_today INT NOT NULL DEFAULT 0,
                    score FLOAT,
                    last_updated TIMESTAMPTZ DEFAULT NOW(),
                    promoted BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE(symbol, exchange_long, exchange_short)
                );

                CREATE TABLE IF NOT EXISTS spread_events (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange_long TEXT NOT NULL,
                    exchange_short TEXT NOT NULL,
                    spread_pct FLOAT NOT NULL,
                    zscore FLOAT,
                    event_type TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

    async def insert_paper_position(self, data: dict) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO paper_positions (symbol, exchange_long, exchange_short,
                    spread_pct_entry, size_usdt, fee_usdt)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, data["symbol"], data["exchange_long"], data["exchange_short"],
                data["spread_pct"], 10.0, data.get("fee_usdt", 0.008))
            return row["id"]

    async def close_paper_position(self, position_id: int, spread_pct_exit: float) -> None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_positions WHERE id = $1", position_id)
            if not row:
                return
            hold_minutes = (
                (datetime.datetime.now(datetime.timezone.utc) - row["opened_at"])
                .total_seconds() / 60
            )
            gross_pnl = (row["spread_pct_entry"] - spread_pct_exit) / 100 * row["size_usdt"]
            net_pnl = gross_pnl - row["fee_usdt"]
            await conn.execute("""
                UPDATE paper_positions
                SET closed_at = NOW(), hold_minutes = $1,
                    gross_pnl_usdt = $2, net_pnl_usdt = $3, status = 'closed'
                WHERE id = $4
            """, hold_minutes, gross_pnl, net_pnl, position_id)

    async def upsert_pair_stats(self, symbol: str, ex_long: str, ex_short: str) -> None:
        """Recalculate stats from paper_positions for this pair."""
        async with self.pool.acquire() as conn:
            stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE net_pnl_usdt > 0) as wins,
                    AVG(hold_minutes) as avg_hold,
                    AVG(net_pnl_usdt) as avg_pnl
                FROM paper_positions
                WHERE symbol=$1 AND exchange_long=$2 AND exchange_short=$3
                AND status='closed'
            """, symbol, ex_long, ex_short)

            total = stats["total"] or 0
            wins = stats["wins"] or 0
            win_rate = wins / total if total > 0 else 0
            score = round(
                win_rate * 60
                + min(total / 100, 1) * 20
                + (1 if (stats["avg_hold"] or 99) < 20 else 0) * 20,
                1,
            )

            await conn.execute("""
                INSERT INTO pair_stats (symbol, exchange_long, exchange_short,
                    total_trades, win_count, avg_hold_min, avg_net_pnl, score, last_updated)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (symbol, exchange_long, exchange_short)
                DO UPDATE SET total_trades=$4, win_count=$5, avg_hold_min=$6,
                    avg_net_pnl=$7, score=$8, last_updated=NOW()
            """, symbol, ex_long, ex_short, total, wins,
                stats["avg_hold"], stats["avg_pnl"], score)

    async def get_queue_candidates(
        self, min_score: float = 75.0, min_trades: int = 50
    ) -> list:
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT * FROM pair_stats
                WHERE score >= $1 AND total_trades >= $2 AND promoted = FALSE
                ORDER BY score DESC
            """, min_score, min_trades)
