-- Create P&L daily aggregation (idempotent)
CREATE TABLE IF NOT EXISTS pnl_daily (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  day           TEXT NOT NULL,                         -- 'YYYY-MM-DD'
  symbol        TEXT NOT NULL,
  realized_pnl  REAL NOT NULL DEFAULT 0,
  unreal_pnl    REAL NOT NULL DEFAULT 0,
  fees_usd      REAL NOT NULL DEFAULT 0,
  net_pnl       REAL NOT NULL DEFAULT 0,               -- realized - fees (+ optional unreal)
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(day, symbol)
);
