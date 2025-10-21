-- Migration: Add indexes to trades table for performance
-- Date: 2025-10-21
-- Purpose: Optimize queries on entry_time and status for PNL calculations

-- Index on entry_time (most used in date range queries)
CREATE INDEX IF NOT EXISTS idx_trades_entry_time 
ON trades(entry_time);

-- Index on status (filter CLOSED trades)
CREATE INDEX IF NOT EXISTS idx_trades_status 
ON trades(status);

-- Composite index on entry_time + status (optimal for our main query)
CREATE INDEX IF NOT EXISTS idx_trades_entry_time_status 
ON trades(entry_time, status);

-- Index on symbol (for per-symbol queries)
CREATE INDEX IF NOT EXISTS idx_trades_symbol 
ON trades(symbol);

-- Index on trade_id (for lookups)
CREATE INDEX IF NOT EXISTS idx_trades_trade_id 
ON trades(trade_id);

-- Composite index for symbol + entry_time (for per-symbol date range queries)
CREATE INDEX IF NOT EXISTS idx_trades_symbol_entry_time 
ON trades(symbol, entry_time);