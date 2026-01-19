-- =============================================
-- TRADING BOT - PostgreSQL Schema
-- Migrated from SQLite
-- =============================================

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================
-- ORDERS
-- =============================================
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    symbol VARCHAR(24) NOT NULL,
    side VARCHAR(4) NOT NULL,
    type VARCHAR(6) DEFAULT 'LIMIT' NOT NULL,
    tif VARCHAR(3) DEFAULT 'GTC' NOT NULL,
    qty NUMERIC(28, 12) NOT NULL,
    price NUMERIC(28, 12),
    filled_qty NUMERIC(28, 12) DEFAULT 0 NOT NULL,
    avg_fill_price NUMERIC(28, 12),
    status VARCHAR(16) DEFAULT 'NEW' NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_event_at TIMESTAMP,
    canceled_at TIMESTAMP,
    strategy_tag VARCHAR(64),
    reduce_only BOOLEAN DEFAULT FALSE NOT NULL,
    post_only BOOLEAN DEFAULT FALSE NOT NULL,
    client_order_id VARCHAR(64) NOT NULL,
    exchange_order_id VARCHAR(64),
    note VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    revision INTEGER DEFAULT 1 NOT NULL,
    CONSTRAINT uq_orders_ws_client_id UNIQUE (workspace_id, client_order_id)
);

-- =============================================
-- POSITIONS
-- =============================================
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    symbol VARCHAR(24) NOT NULL,
    side VARCHAR(4) NOT NULL,
    qty NUMERIC(28, 12) NOT NULL,
    entry_price NUMERIC(28, 12) NOT NULL,
    last_mark_price NUMERIC(28, 12),
    realized_pnl NUMERIC(28, 12) DEFAULT 0 NOT NULL,
    unrealized_pnl NUMERIC(28, 12),
    status VARCHAR(6) DEFAULT 'OPEN' NOT NULL,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    is_open BOOLEAN DEFAULT TRUE NOT NULL,
    note VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    revision INTEGER DEFAULT 1 NOT NULL
);

-- =============================================
-- SESSIONS
-- =============================================
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    name VARCHAR(64),
    description VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- =============================================
-- UI_STATE
-- =============================================
CREATE TABLE IF NOT EXISTS ui_state (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    watchlist JSONB NOT NULL,
    layout JSONB NOT NULL,
    ui_prefs JSONB NOT NULL,
    revision BIGINT DEFAULT 1 NOT NULL,
    active BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_ui_state_workspace UNIQUE (workspace_id)
);

-- =============================================
-- STRATEGY_STATE
-- =============================================
CREATE TABLE IF NOT EXISTS strategy_state (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    per_symbol JSONB NOT NULL,
    revision BIGINT DEFAULT 1 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_strategy_state_workspace UNIQUE (workspace_id)
);

-- =============================================
-- PNL_LEDGER
-- =============================================
CREATE TABLE IF NOT EXISTS pnl_ledger (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMP NOT NULL,
    exchange VARCHAR(64) NOT NULL,
    account_id VARCHAR(128) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    base_asset VARCHAR(32) NOT NULL,
    quote_asset VARCHAR(32) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    amount_asset NUMERIC(38, 18) NOT NULL,
    amount_usd NUMERIC(38, 18) NOT NULL,
    ref_order_id VARCHAR(128),
    ref_trade_id VARCHAR(128),
    meta JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pnl_ledger_ts ON pnl_ledger(ts);

-- =============================================
-- PNL_DAILY
-- =============================================
CREATE TABLE IF NOT EXISTS pnl_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    exchange VARCHAR(64) NOT NULL,
    account_id VARCHAR(128) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    realized_usd NUMERIC(38, 18) NOT NULL,
    fees_usd NUMERIC(38, 18) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pnl_daily_unique_idx UNIQUE (date, exchange, account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_pnl_daily_date ON pnl_daily(date);
CREATE INDEX IF NOT EXISTS idx_pnl_daily_symbol ON pnl_daily(symbol);

-- =============================================
-- TRADES
-- =============================================
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    exchange VARCHAR,
    entry_time TIMESTAMP NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_qty DOUBLE PRECISION NOT NULL,
    entry_side VARCHAR,
    exit_time TIMESTAMP,
    exit_price DOUBLE PRECISION,
    exit_qty DOUBLE PRECISION,
    exit_side VARCHAR,
    exit_reason VARCHAR,
    pnl_usd DOUBLE PRECISION,
    pnl_bps DOUBLE PRECISION,
    pnl_percent DOUBLE PRECISION,
    entry_fee DOUBLE PRECISION,
    exit_fee DOUBLE PRECISION,
    total_fee DOUBLE PRECISION,
    hold_duration_sec DOUBLE PRECISION,
    spread_bps_entry DOUBLE PRECISION,
    imbalance_entry DOUBLE PRECISION,
    depth_5bps_entry DOUBLE PRECISION,
    strategy_tag VARCHAR,
    strategy_params TEXT,
    status VARCHAR,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP
);

-- =============================================
-- FILLS
-- =============================================
CREATE TABLE IF NOT EXISTS fills (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER DEFAULT 1 NOT NULL,
    order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    symbol VARCHAR(24) NOT NULL,
    side VARCHAR(4) NOT NULL,
    qty NUMERIC(28, 12) NOT NULL,
    price NUMERIC(28, 12) NOT NULL,
    quote_qty NUMERIC(28, 12),
    fee NUMERIC(28, 12) DEFAULT 0 NOT NULL,
    fee_asset VARCHAR(16),
    liquidity VARCHAR(5),
    is_maker BOOLEAN DEFAULT FALSE NOT NULL,
    client_order_id VARCHAR(64),
    exchange_order_id VARCHAR(64),
    trade_id VARCHAR(64),
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strategy_tag VARCHAR(64),
    note VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    revision INTEGER DEFAULT 1 NOT NULL,
    CONSTRAINT uq_fills_ws_symbol_trade UNIQUE (workspace_id, symbol, trade_id)
);

-- =============================================
-- ML_SNAPSHOTS
-- =============================================
CREATE TABLE IF NOT EXISTS ml_snapshots (
    id SERIAL PRIMARY KEY,
    ts BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    bid DOUBLE PRECISION,
    ask DOUBLE PRECISION,
    mid DOUBLE PRECISION,
    last DOUBLE PRECISION,
    spread_bps DOUBLE PRECISION,
    eff_spread_bps_maker DOUBLE PRECISION,
    depth5_bid_usd DOUBLE PRECISION,
    depth5_ask_usd DOUBLE PRECISION,
    depth10_bid_usd DOUBLE PRECISION,
    depth10_ask_usd DOUBLE PRECISION,
    imbalance DOUBLE PRECISION,
    trades_per_min DOUBLE PRECISION,
    usd_per_min DOUBLE PRECISION,
    median_trade_usd DOUBLE PRECISION,
    atr1m_pct DOUBLE PRECISION,
    grinder_ratio DOUBLE PRECISION,
    pullback_median_retrace DOUBLE PRECISION,
    spike_count_90m INTEGER,
    imbalance_sigma_hits_60m INTEGER,
    ws_lag_ms INTEGER,
    your_offset_bps DOUBLE PRECISION,
    spread_volatility_5min DOUBLE PRECISION,
    filled_20s BOOLEAN DEFAULT NULL,
    fill_time_s DOUBLE PRECISION DEFAULT NULL,
    mid_at_fill DOUBLE PRECISION DEFAULT NULL,
    mid_at_20s DOUBLE PRECISION DEFAULT NULL,
    profit_bps DOUBLE PRECISION DEFAULT NULL,
    exit_spread_bps DOUBLE PRECISION DEFAULT NULL,
    scanner_preset TEXT,
    ml_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ml_snapshots_ts ON ml_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_ml_snapshots_symbol ON ml_snapshots(symbol);

-- =============================================
-- ML_TRADE_OUTCOMES
-- =============================================
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    outcome TEXT,
    pnl_bps DOUBLE PRECISION,
    features JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================
-- DONE!
-- =============================================