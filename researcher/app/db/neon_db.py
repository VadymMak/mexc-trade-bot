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
            # ML dataset columns — features & labels for model training
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS exit_reason     TEXT",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS entry_mode      TEXT",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS spread_mean     NUMERIC(10,6)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS spread_std      NUMERIC(10,6)",
            # Flow features (tape + order book at entry time)
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS buy_pressure    NUMERIC(6,4)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS trade_velocity  NUMERIC(8,2)",
            "ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS book_imbalance  NUMERIC(6,4)",
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
        # Migrate pair_stats — add columns missing from older schema versions
        for col_ddl in [
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS win_trades       INT           NOT NULL DEFAULT 0",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS total_gross_pnl  NUMERIC(14,4) NOT NULL DEFAULT 0",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS total_net_pnl    NUMERIC(14,4) NOT NULL DEFAULT 0",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS total_fees       NUMERIC(14,4) NOT NULL DEFAULT 0",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS avg_hold_seconds NUMERIC",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS sharpe           NUMERIC(8,4)",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS max_drawdown_pct NUMERIC(8,4)",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS score            NUMERIC(6,2)",
            "ALTER TABLE pair_stats ADD COLUMN IF NOT EXISTS promoted         BOOLEAN       NOT NULL DEFAULT FALSE",
        ]:
            await self._pool.execute(col_ddl)
        # Symbol lifecycle table — TESTING / APPROVED / BLACKLISTED
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS symbol_states (
                symbol            TEXT         NOT NULL,
                state             TEXT         NOT NULL DEFAULT 'TESTING',
                test_started_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                evaluated_at      TIMESTAMPTZ,
                retest_after      TIMESTAMPTZ,
                total_trades      INT          NOT NULL DEFAULT 0,
                tp_rate           NUMERIC(6,4),
                net_pnl_usdt      NUMERIC(14,4),
                reason            TEXT,
                PRIMARY KEY (symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_symbol_states_state
                ON symbol_states(state);
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
        entry_mode:           Optional[str]   = None,
        spread_mean:          Optional[float] = None,
        spread_std:           Optional[float] = None,
        buy_pressure:         Optional[float] = None,
        trade_velocity:       Optional[float] = None,
        book_imbalance:       Optional[float] = None,
    ) -> int:
        """Insert open position; returns its auto-generated id."""
        assert self._pool
        row = await self._pool.fetchrow(
            """
            INSERT INTO paper_positions
                (symbol, exchange_long, exchange_short,
                 entry_spread_pct, entry_zscore,
                 deal_size_usdt, slippage_entry_usdt, fee_usdt,
                 entry_mode, spread_mean, spread_std,
                 buy_pressure, trade_velocity, book_imbalance)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING id
            """,
            symbol, exchange_long, exchange_short,
            entry_spread_pct, entry_zscore,
            deal_size_usdt, slippage_entry_usdt, fee_usdt,
            entry_mode, spread_mean, spread_std,
            buy_pressure, trade_velocity, book_imbalance,
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
        exit_reason:         Optional[str] = None,
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
                closed_at           = NOW(),
                exit_reason         = $8
            WHERE id = $1
            """,
            pos_id, exit_spread_pct, exit_zscore,
            slippage_exit_usdt, gross_pnl_usdt, net_pnl_usdt, hold_seconds,
            exit_reason,
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

    async def export_dataset_csv(self) -> str:
        """
        Export all closed paper_positions as a CSV string for ML training.

        Features (known at entry time):
          symbol, exchange_long, exchange_short, entry_mode,
          entry_spread_pct, entry_zscore, spread_mean, spread_std,
          spread_zscore_ratio (entry_spread / spread_mean),
          spread_cv (spread_std / spread_mean — coefficient of variation),
          hour_of_day, day_of_week, deal_size_usdt

        Labels (outcome):
          exit_reason, exit_spread_pct, exit_zscore,
          hold_seconds, gross_pnl_usdt, net_pnl_usdt,
          pnl_pct (net_pnl / deal_size * 100),
          profitable (1 if net_pnl > 0 else 0)
        """
        import csv, io
        assert self._pool
        rows = await self._pool.fetch(
            """
            SELECT
                symbol, exchange_long, exchange_short,
                entry_mode, entry_spread_pct, entry_zscore,
                spread_mean, spread_std,
                buy_pressure, trade_velocity, book_imbalance,
                exit_reason, exit_spread_pct, exit_zscore,
                deal_size_usdt, gross_pnl_usdt, net_pnl_usdt,
                hold_seconds, opened_at, closed_at
            FROM paper_positions
            WHERE status = 'closed'
              AND entry_spread_pct IS NOT NULL
            ORDER BY opened_at ASC
            """,
        )
        if not rows:
            return "no data"

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            # --- identifiers ---
            "symbol", "exchange_long", "exchange_short",
            # --- entry features ---
            "entry_mode", "entry_spread_pct", "entry_zscore",
            "spread_mean", "spread_std",
            "spread_zscore_ratio",   # entry_spread / spread_mean
            "spread_cv",             # spread_std / spread_mean (volatility)
            # --- flow features (tape + book at entry time) ---
            "buy_pressure",          # buy_vol / total_vol last 60s (0–1)
            "trade_velocity",        # trades per minute last 60s
            "book_imbalance",        # (bid_qty - ask_qty) / total top-5 (-1…+1)
            # --- time features (UTC) ---
            "hour_utc", "day_of_week",  # 0=Mon…6=Sun
            "trading_session",          # asia / europe / overlap / us / quiet
            "is_weekend",               # 1 if Sat/Sun
            "mins_to_funding",          # minutes until next 00/08/16h UTC funding
            "deal_size_usdt",
            # --- outcome labels ---
            "exit_reason", "exit_spread_pct", "exit_zscore",
            "hold_seconds", "gross_pnl_usdt", "net_pnl_usdt",
            "pnl_pct", "profitable",
        ])

        for r in rows:
            e_spread  = float(r["entry_spread_pct"] or 0)
            s_mean    = float(r["spread_mean"] or 0)
            s_std     = float(r["spread_std"] or 0)
            net_pnl   = float(r["net_pnl_usdt"] or 0)
            deal_size = float(r["deal_size_usdt"] or 10)
            opened_at = r["opened_at"]

            spread_zscore_ratio = round(e_spread / s_mean, 4) if s_mean > 0 else None
            spread_cv           = round(s_std / s_mean, 4)    if s_mean > 0 else None
            pnl_pct             = round(net_pnl / deal_size * 100, 4)
            profitable          = 1 if net_pnl > 0 else 0

            if opened_at:
                hour_utc       = opened_at.hour
                dow            = opened_at.weekday()  # 0=Mon … 6=Sun
                is_weekend     = 1 if dow >= 5 else 0
                session        = _trading_session(hour_utc)
                ts_ms          = int(opened_at.timestamp() * 1000)
                mins_to_fund   = _mins_to_funding(ts_ms)
            else:
                hour_utc = dow = is_weekend = session = mins_to_fund = ""

            writer.writerow([
                r["symbol"], r["exchange_long"], r["exchange_short"],
                r["entry_mode"] or "", e_spread,
                round(float(r["entry_zscore"]), 4) if r["entry_zscore"] is not None else "",
                round(s_mean, 6) if s_mean else "", round(s_std, 6) if s_std else "",
                spread_zscore_ratio, spread_cv,
                round(float(r["buy_pressure"]),   4) if r["buy_pressure"]   is not None else "",
                round(float(r["trade_velocity"]), 2) if r["trade_velocity"] is not None else "",
                round(float(r["book_imbalance"]), 4) if r["book_imbalance"] is not None else "",
                hour_utc, dow, session, is_weekend,
                mins_to_fund,
                deal_size,
                r["exit_reason"] or "", float(r["exit_spread_pct"] or 0),
                round(float(r["exit_zscore"]), 4) if r["exit_zscore"] is not None else "",
                r["hold_seconds"] or 0,
                round(float(r["gross_pnl_usdt"] or 0), 6), round(net_pnl, 6),
                pnl_pct, profitable,
            ])

        return buf.getvalue()

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

    # ── symbol_states ─────────────────────────────────────────────────────────

    async def ensure_symbol_testing(self, symbol: str) -> None:
        """Insert symbol in TESTING state if not already tracked."""
        assert self._pool
        await self._pool.execute(
            """
            INSERT INTO symbol_states (symbol, state, test_started_at)
            VALUES ($1, 'TESTING', NOW())
            ON CONFLICT (symbol) DO NOTHING
            """,
            symbol,
        )

    async def get_symbol_state(self, symbol: str) -> Optional[str]:
        """Return current state for symbol, or None if not yet tracked."""
        assert self._pool
        row = await self._pool.fetchrow(
            "SELECT state, retest_after FROM symbol_states WHERE symbol = $1",
            symbol,
        )
        if row is None:
            return None
        # If blacklisted but retest_after has passed → back to TESTING
        if row["state"] == "BLACKLISTED" and row["retest_after"] is not None:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) >= row["retest_after"]:
                await self._pool.execute(
                    """
                    UPDATE symbol_states
                    SET state='TESTING', test_started_at=NOW(),
                        evaluated_at=NULL, retest_after=NULL, reason=NULL
                    WHERE symbol=$1
                    """,
                    symbol,
                )
                return "TESTING"
        return row["state"]

    async def update_symbol_state(
        self,
        symbol:       str,
        state:        str,
        total_trades: int,
        tp_rate:      float,
        net_pnl:      float,
        reason:       str,
        retest_days:  int = 30,
    ) -> None:
        """Set symbol state to APPROVED or BLACKLISTED after evaluation."""
        assert self._pool
        from datetime import datetime, timezone, timedelta
        retest_after = (
            (datetime.now(timezone.utc) + timedelta(days=retest_days))
            if state == "BLACKLISTED" else None
        )
        await self._pool.execute(
            """
            INSERT INTO symbol_states
                (symbol, state, test_started_at, evaluated_at,
                 retest_after, total_trades, tp_rate, net_pnl_usdt, reason)
            VALUES ($1,$2,NOW(),NOW(),$3,$4,$5,$6,$7)
            ON CONFLICT (symbol) DO UPDATE SET
                state        = EXCLUDED.state,
                evaluated_at = NOW(),
                retest_after = $3,
                total_trades = EXCLUDED.total_trades,
                tp_rate      = EXCLUDED.tp_rate,
                net_pnl_usdt = EXCLUDED.net_pnl_usdt,
                reason       = EXCLUDED.reason
            """,
            symbol, state, retest_after,
            total_trades, tp_rate, net_pnl, reason,
        )

    async def get_all_symbol_states(self) -> list[dict]:
        """Return all symbol states for monitoring / API."""
        assert self._pool
        rows = await self._pool.fetch(
            """
            SELECT symbol, state, test_started_at, evaluated_at,
                   retest_after, total_trades, tp_rate, net_pnl_usdt, reason
            FROM symbol_states
            ORDER BY state, net_pnl_usdt DESC NULLS LAST
            """
        )
        return [dict(r) for r in rows]


# ── Time session helper ───────────────────────────────────────────────────────

def _trading_session(hour_utc: int) -> str:
    """
    Classify UTC hour into a named crypto trading session.

    Sessions (UTC):
      asia      00-06  — Tokyo + Sydney dominant
      europe    07-12  — London open, EU activity picks up
      overlap   13-15  — EU + US both active (highest liquidity)
      us        16-21  — New York session dominant
      quiet     22-23  — Low volume, thin order books

    Model hypothesis: overlap hours may have tighter real spreads
    (arbitrage closes faster → more TAKE_PROFIT), while quiet/asia
    hours may have wider, stickier spreads → more TIME_STOP losses.
    """
    if 0 <= hour_utc <= 6:
        return "asia"
    elif 7 <= hour_utc <= 12:
        return "europe"
    elif 13 <= hour_utc <= 15:
        return "overlap"
    elif 16 <= hour_utc <= 21:
        return "us"
    else:  # 22-23
        return "quiet"


# ── Funding time helper ───────────────────────────────────────────────────────

_FUNDING_TIMES_SEC = (0, 28_800, 57_600)  # 00:00, 08:00, 16:00 UTC


def _mins_to_funding(ts_ms: int) -> float:
    """Minutes until the next perpetual futures funding payment."""
    now_sec = (ts_ms // 1000) % 86_400
    gaps = [((f - now_sec) % 86_400) for f in _FUNDING_TIMES_SEC]
    return round(min(gaps) / 60, 2)


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
