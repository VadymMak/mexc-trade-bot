-- Create ML Trade Outcomes table with ALL features
-- Date: 2025-11-06 (updated 2025-11-13)
-- Complete table with 77 columns including exploration_mode

CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'mexc',
    workspace_id INTEGER DEFAULT 1,
    
    -- Entry
    entry_time TIMESTAMP NOT NULL,
    entry_price REAL NOT NULL,
    entry_qty REAL NOT NULL,
    entry_side TEXT DEFAULT 'BUY',
    
    -- Base spread features
    spread_bps_entry REAL DEFAULT 0.0,
    spread_pct_entry REAL DEFAULT 0.0,
    spread_abs_entry REAL DEFAULT 0.0,
    imbalance_entry REAL DEFAULT 0.5,
    
    -- Effective spreads
    eff_spread_bps_entry REAL DEFAULT 0.0,
    eff_spread_pct_entry REAL DEFAULT 0.0,
    eff_spread_abs_entry REAL DEFAULT 0.0,
    eff_spread_maker_bps_entry REAL DEFAULT 0.0,
    eff_spread_taker_bps_entry REAL DEFAULT 0.0,
    
    -- Depth features
    depth5_bid_usd_entry REAL DEFAULT 0.0,
    depth5_ask_usd_entry REAL DEFAULT 0.0,
    depth10_bid_usd_entry REAL DEFAULT 0.0,
    depth10_ask_usd_entry REAL DEFAULT 0.0,
    
    -- Volume features
    base_volume_24h_entry REAL DEFAULT 0.0,
    quote_volume_24h_entry REAL DEFAULT 0.0,
    trades_per_min_entry REAL DEFAULT 0.0,
    usd_per_min_entry REAL DEFAULT 0.0,
    median_trade_usd_entry REAL DEFAULT 0.0,
    
    -- Fee structure
    maker_fee_entry REAL DEFAULT 0.0,
    taker_fee_entry REAL DEFAULT 0.0,
    zero_fee_entry INTEGER DEFAULT 0,
    
    -- Volatility features (from candles)
    atr1m_pct_entry REAL DEFAULT 0.0,
    spike_count_90m_entry INTEGER DEFAULT 0,
    grinder_ratio_entry REAL DEFAULT 0.0,
    pullback_median_retrace_entry REAL DEFAULT 0.35,
    range_stable_pct_entry REAL DEFAULT 0.0,
    vol_pattern_entry INTEGER DEFAULT 0,
    
    -- Pattern scores
    dca_potential_entry INTEGER DEFAULT 0,
    scanner_score_entry REAL DEFAULT 0.0,
    ws_lag_ms_entry INTEGER DEFAULT 0,
    
    -- Derived features
    depth_imbalance_entry REAL DEFAULT 1.0,
    depth5_total_usd_entry REAL DEFAULT 0.0,
    depth10_total_usd_entry REAL DEFAULT 0.0,
    depth_ratio_5_to_10_entry REAL DEFAULT 0.5,
    spread_to_depth5_ratio_entry REAL DEFAULT 0.0,
    volume_to_depth_ratio_entry REAL DEFAULT 0.0,
    trades_per_dollar_entry REAL DEFAULT 0.0,
    avg_trade_size_entry REAL DEFAULT 0.0,
    mid_price_entry REAL DEFAULT 0.0,
    price_precision_entry INTEGER DEFAULT 0,
    
    -- Time context
    hour_of_day INTEGER,
    day_of_week INTEGER,
    minute_of_hour INTEGER,
    
    -- Strategy parameters
    take_profit_bps REAL,
    stop_loss_bps REAL,
    trailing_stop_enabled INTEGER DEFAULT 0,
    trail_activation_bps REAL,
    trail_distance_bps REAL,
    timeout_seconds REAL,
    exploration_mode INTEGER DEFAULT 0,
    
    -- Exit
    exit_time TIMESTAMP,
    exit_price REAL,
    exit_qty REAL,
    exit_reason TEXT,
    
    -- Outcome
    pnl_usd REAL,
    pnl_bps REAL,
    pnl_percent REAL,
    hold_duration_sec REAL,
    
    -- Performance metrics
    max_favorable_excursion_bps REAL,
    max_adverse_excursion_bps REAL,
    peak_price REAL,
    lowest_price REAL,
    
    -- ML labels
    win INTEGER DEFAULT 0,
    hit_tp INTEGER DEFAULT 0,
    hit_sl INTEGER DEFAULT 0,
    hit_trailing INTEGER DEFAULT 0,
    timed_out INTEGER DEFAULT 0,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_symbol ON ml_trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_entry_time ON ml_trade_outcomes(entry_time);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_exit_reason ON ml_trade_outcomes(exit_reason);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_workspace ON ml_trade_outcomes(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_exploration ON ml_trade_outcomes(exploration_mode);
CREATE INDEX IF NOT EXISTS idx_ml_outcomes_win ON ml_trade_outcomes(win);