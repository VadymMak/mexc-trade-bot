-- Migration: Create ml_trade_outcomes table for PostgreSQL
-- Date: 2026-01-17
-- Description: Table for ML feature collection and trade analysis (80+ features)
-- This replaces the SQLite version for Railway PostgreSQL

-- Create table if not exists
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id SERIAL PRIMARY KEY,
    trade_id TEXT,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'mexc',
    workspace_id INTEGER DEFAULT 1,
    
    -- Entry data
    entry_time TIMESTAMP WITH TIME ZONE,
    entry_price NUMERIC,
    entry_qty NUMERIC,
    entry_side TEXT,
    
    -- Spread/Depth at entry
    spread_bps_entry NUMERIC,
    eff_spread_bps_entry NUMERIC,
    depth5_bid_usd_entry NUMERIC,
    depth5_ask_usd_entry NUMERIC,
    depth10_bid_usd_entry NUMERIC,
    depth10_ask_usd_entry NUMERIC,
    imbalance_entry NUMERIC,
    
    -- Market conditions at entry
    atr1m_pct_entry NUMERIC,
    grinder_ratio_entry NUMERIC,
    pullback_median_retrace_entry NUMERIC,
    trades_per_min_entry NUMERIC,
    usd_per_min_entry NUMERIC,
    median_trade_usd_entry NUMERIC,
    
    -- Spread details
    spread_pct_entry NUMERIC,
    spread_abs_entry NUMERIC,
    eff_spread_pct_entry NUMERIC,
    eff_spread_abs_entry NUMERIC,
    eff_spread_maker_bps_entry NUMERIC,
    eff_spread_taker_bps_entry NUMERIC,
    
    -- Volume
    base_volume_24h_entry NUMERIC,
    quote_volume_24h_entry NUMERIC,
    
    -- Fees
    maker_fee_entry NUMERIC,
    taker_fee_entry NUMERIC,
    zero_fee_entry INTEGER,
    
    -- Patterns
    spike_count_90m_entry INTEGER,
    range_stable_pct_entry NUMERIC,
    vol_pattern_entry INTEGER,
    dca_potential_entry INTEGER,
    scanner_score_entry NUMERIC,
    
    -- Orderbook analysis
    ws_lag_ms_entry INTEGER,
    depth_imbalance_entry NUMERIC,
    depth5_total_usd_entry NUMERIC,
    depth10_total_usd_entry NUMERIC,
    depth_ratio_5_to_10_entry NUMERIC,
    spread_to_depth5_ratio_entry NUMERIC,
    volume_to_depth_ratio_entry NUMERIC,
    trades_per_dollar_entry NUMERIC,
    avg_trade_size_entry NUMERIC,
    
    -- Price
    mid_price_entry NUMERIC,
    price_precision_entry INTEGER,
    
    -- Market quality
    spoofing_score_entry NUMERIC,
    spread_stability_entry NUMERIC,
    order_lifetime_avg_entry NUMERIC,
    book_refresh_rate_entry NUMERIC,
    
    -- MM detection
    mm_detected_entry INTEGER,
    mm_confidence_entry NUMERIC,
    mm_safe_size_entry NUMERIC,
    mm_lower_bound_entry NUMERIC,
    mm_upper_bound_entry NUMERIC,
    
    -- Time features (important for Brain learning!)
    hour_of_day INTEGER,
    day_of_week INTEGER,
    minute_of_hour INTEGER,
    
    -- Strategy params
    take_profit_bps NUMERIC,
    stop_loss_bps NUMERIC,
    trailing_stop_enabled INTEGER,
    trail_activation_bps NUMERIC,
    trail_distance_bps NUMERIC,
    timeout_seconds NUMERIC,
    exploration_mode INTEGER,
    
    -- Exit data
    exit_time TIMESTAMP WITH TIME ZONE,
    exit_price NUMERIC,
    exit_qty NUMERIC,
    exit_reason TEXT,
    pnl_usd NUMERIC,
    pnl_bps NUMERIC,
    pnl_percent NUMERIC,
    hold_duration_sec NUMERIC,
    
    -- Price tracking (for MFE/MAE analysis)
    max_favorable_excursion_bps NUMERIC,
    max_adverse_excursion_bps NUMERIC,
    peak_price NUMERIC,
    lowest_price NUMERIC,
    
    -- Outcome flags
    win INTEGER,
    hit_tp INTEGER,
    hit_sl INTEGER,
    hit_trailing INTEGER,
    timed_out INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_symbol ON ml_trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_created ON ml_trade_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_exit_reason ON ml_trade_outcomes(exit_reason);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_hour ON ml_trade_outcomes(hour_of_day);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_day ON ml_trade_outcomes(day_of_week);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_trade_id ON ml_trade_outcomes(trade_id);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_win ON ml_trade_outcomes(win);

-- Composite index for time-based pattern analysis
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_time_pattern 
ON ml_trade_outcomes(symbol, hour_of_day, day_of_week, exit_reason);

-- Record migration (if _migrations table exists)
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = '_migrations') THEN
        INSERT INTO _migrations (name, applied_at) 
        VALUES ('20260117_create_ml_trade_outcomes_postgres', NOW())
        ON CONFLICT (name) DO NOTHING;
    END IF;
END $$;

-- Verify table created
SELECT 'ml_trade_outcomes table created successfully!' as status, 
       COUNT(*) as columns 
FROM information_schema.columns 
WHERE table_name = 'ml_trade_outcomes';