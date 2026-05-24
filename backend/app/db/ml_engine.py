"""
Separate database engine for ML trade outcomes (NeonDB/PostgreSQL).
Uses ML_DATABASE_URL env var. Falls back to main SQLite SessionLocal if not set.
"""
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)

ML_DATABASE_URL = os.getenv("ML_DATABASE_URL", "")

if ML_DATABASE_URL:
    if ML_DATABASE_URL.startswith("postgres://"):
        ML_DATABASE_URL = ML_DATABASE_URL.replace("postgres://", "postgresql://", 1)

    ml_engine = create_engine(
        ML_DATABASE_URL,
        pool_size=3,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={"sslmode": "require"},
    )
    MLSessionLocal = sessionmaker(
        bind=ml_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    ML_DB_ENABLED = True
    log.info("✅ ML NeonDB engine initialized (ML_DATABASE_URL)")
else:
    from app.db.session import SessionLocal as MLSessionLocal  # type: ignore[assignment]
    ml_engine = None
    ML_DB_ENABLED = False
    log.warning("⚠️ ML_DATABASE_URL not set — ml_trade_outcomes using SQLite fallback")


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id SERIAL PRIMARY KEY,
    trade_id TEXT,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'mexc',
    workspace_id INTEGER DEFAULT 1,
    entry_time TIMESTAMP WITH TIME ZONE,
    entry_price NUMERIC,
    entry_qty NUMERIC,
    entry_side TEXT,
    spread_bps_entry NUMERIC,
    eff_spread_bps_entry NUMERIC,
    depth5_bid_usd_entry NUMERIC,
    depth5_ask_usd_entry NUMERIC,
    depth10_bid_usd_entry NUMERIC,
    depth10_ask_usd_entry NUMERIC,
    imbalance_entry NUMERIC,
    atr1m_pct_entry NUMERIC,
    grinder_ratio_entry NUMERIC,
    pullback_median_retrace_entry NUMERIC,
    trades_per_min_entry NUMERIC,
    usd_per_min_entry NUMERIC,
    median_trade_usd_entry NUMERIC,
    spread_pct_entry NUMERIC,
    spread_abs_entry NUMERIC,
    eff_spread_pct_entry NUMERIC,
    eff_spread_abs_entry NUMERIC,
    eff_spread_maker_bps_entry NUMERIC,
    eff_spread_taker_bps_entry NUMERIC,
    base_volume_24h_entry NUMERIC,
    quote_volume_24h_entry NUMERIC,
    maker_fee_entry NUMERIC,
    taker_fee_entry NUMERIC,
    zero_fee_entry INTEGER,
    spike_count_90m_entry INTEGER,
    range_stable_pct_entry NUMERIC,
    vol_pattern_entry INTEGER,
    dca_potential_entry INTEGER,
    scanner_score_entry NUMERIC,
    ws_lag_ms_entry INTEGER,
    depth_imbalance_entry NUMERIC,
    depth5_total_usd_entry NUMERIC,
    depth10_total_usd_entry NUMERIC,
    depth_ratio_5_to_10_entry NUMERIC,
    spread_to_depth5_ratio_entry NUMERIC,
    volume_to_depth_ratio_entry NUMERIC,
    trades_per_dollar_entry NUMERIC,
    avg_trade_size_entry NUMERIC,
    mid_price_entry NUMERIC,
    price_precision_entry INTEGER,
    spoofing_score_entry NUMERIC,
    spread_stability_entry NUMERIC,
    order_lifetime_avg_entry NUMERIC,
    book_refresh_rate_entry NUMERIC,
    mm_detected_entry INTEGER,
    mm_confidence_entry NUMERIC,
    mm_safe_size_entry NUMERIC,
    mm_lower_bound_entry NUMERIC,
    mm_upper_bound_entry NUMERIC,
    hour_of_day INTEGER,
    day_of_week INTEGER,
    minute_of_hour INTEGER,
    entry_reason_text TEXT,
    top_score_rank INTEGER,
    take_profit_bps NUMERIC,
    stop_loss_bps NUMERIC,
    trailing_stop_enabled INTEGER,
    trail_activation_bps NUMERIC,
    trail_distance_bps NUMERIC,
    timeout_seconds NUMERIC,
    exploration_mode INTEGER,
    exit_time TIMESTAMP WITH TIME ZONE,
    exit_price NUMERIC,
    exit_qty NUMERIC,
    exit_reason TEXT,
    pnl_usd NUMERIC,
    pnl_bps NUMERIC,
    pnl_percent NUMERIC,
    hold_duration_sec NUMERIC,
    max_favorable_excursion_bps NUMERIC,
    max_adverse_excursion_bps NUMERIC,
    peak_price NUMERIC,
    lowest_price NUMERIC,
    win INTEGER,
    hit_tp INTEGER,
    hit_sl INTEGER,
    hit_trailing INTEGER,
    timed_out INTEGER,
    spread_at_exit NUMERIC,
    mm_present_at_exit INTEGER,
    depth_at_exit NUMERIC,
    price_continued_bps NUMERIC,
    ml_score NUMERIC DEFAULT NULL,
    ml_would_block BOOLEAN DEFAULT NULL,
    -- Arb-specific columns (from researcher service)
    entry_zscore NUMERIC DEFAULT NULL,
    exit_zscore NUMERIC DEFAULT NULL,
    exit_spread_pct NUMERIC DEFAULT NULL,
    spread_mean NUMERIC DEFAULT NULL,
    spread_std NUMERIC DEFAULT NULL,
    buy_pressure NUMERIC DEFAULT NULL,
    trade_velocity NUMERIC DEFAULT NULL,
    book_imbalance NUMERIC DEFAULT NULL,
    entry_mode TEXT DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_symbol ON ml_trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_entry_time ON ml_trade_outcomes(entry_time);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_exit_reason ON ml_trade_outcomes(exit_reason);
"""

# Migration SQL: add arb-specific columns to existing table
_MIGRATE_SQL = """
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS entry_zscore       NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS exit_zscore        NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS exit_spread_pct    NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS spread_mean        NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS spread_std         NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS buy_pressure       NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS trade_velocity     NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS book_imbalance     NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS entry_mode         TEXT    DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS is_weekend         INTEGER DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS trading_session    TEXT    DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS mins_to_funding    NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS mexc_spot_basis_pct NUMERIC DEFAULT NULL;
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS price_continued_bps NUMERIC DEFAULT NULL;
"""


def ensure_ml_table() -> None:
    """Create ml_trade_outcomes table in NeonDB if it doesn't exist, then migrate."""
    if not ML_DB_ENABLED or ml_engine is None:
        return
    try:
        with ml_engine.connect() as conn:
            # Create table (idempotent)
            for stmt in _CREATE_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
            # Migrate: add arb-specific columns to existing table (idempotent)
            for stmt in _MIGRATE_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
            conn.commit()
        log.info("✅ ml_trade_outcomes table ensured in NeonDB")
    except Exception as e:
        log.error(f"❌ Failed to create ml_trade_outcomes in NeonDB: {e}")
