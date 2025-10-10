-- Create P&L ledger (idempotent)
CREATE TABLE IF NOT EXISTS pnl_ledger (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms       INTEGER NOT NULL,                        -- event time (ms)
  symbol      TEXT NOT NULL,
  side        TEXT,                                    -- BUY/SELL/FLAT/ADJUST
  qty         REAL NOT NULL DEFAULT 0,
  price       REAL NOT NULL DEFAULT 0,
  fee_usd     REAL NOT NULL DEFAULT 0,
  pnl_usd     REAL NOT NULL DEFAULT 0,
  ref         TEXT,                                    -- orderId/fillId/etc.
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
