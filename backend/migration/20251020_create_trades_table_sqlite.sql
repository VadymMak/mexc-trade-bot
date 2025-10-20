-- Migration: Create trades table for detailed trade logging
-- Date: 2025-10-20
-- Description: Stores every trade entry/exit with P&L, fees, and metadata

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'MEXC',
    entry_time TIMESTAMP NOT NULL,
    entry_price REAL NOT NULL,
    entry_qty REAL NOT NULL,
    entry_side TEXT DEFAULT 'BUY',
    exit_time TIMESTAMP,
    exit_price REAL,
    exit_qty REAL,
    exit_side TEXT,
    exit_reason TEXT,
    pnl_usd REAL DEFAULT 0.0,
    pnl_bps REAL DEFAULT 0.0,
    pnl_percent REAL DEFAULT 0.0,
    entry_fee REAL DEFAULT 0.0,
    exit_fee REAL DEFAULT 0.0,
    total_fee REAL DEFAULT 0.0,
    hold_duration_sec REAL,
    spread_bps_entry REAL,
    imbalance_entry REAL,
    depth_5bps_entry REAL,
    strategy_tag TEXT,
    strategy_params TEXT,
    status TEXT DEFAULT 'CLOSED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, entry_time);