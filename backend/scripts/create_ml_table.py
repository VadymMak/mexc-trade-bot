import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

sql = """
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(24) NOT NULL,
    exchange VARCHAR(16) DEFAULT 'mexc',
    workspace_id INTEGER DEFAULT 1,
    entry_time DATETIME NOT NULL,
    entry_price REAL NOT NULL,
    entry_qty REAL NOT NULL,
    entry_side VARCHAR(4) NOT NULL,
    spread_bps_entry REAL,
    eff_spread_bps_entry REAL,
    depth5_bid_usd_entry REAL,
    depth5_ask_usd_entry REAL,
    depth10_bid_usd_entry REAL,
    depth10_ask_usd_entry REAL,
    imbalance_entry REAL,
    atr1m_pct_entry REAL,
    grinder_ratio_entry REAL,
    pullback_median_retrace_entry REAL,
    trades_per_min_entry REAL,
    usd_per_min_entry REAL,
    median_trade_usd_entry REAL,
    hour_of_day INTEGER,
    day_of_week INTEGER,
    minute_of_hour INTEGER,
    take_profit_bps REAL NOT NULL,
    stop_loss_bps REAL NOT NULL,
    trailing_stop_enabled INTEGER DEFAULT 0,
    trail_activation_bps REAL,
    trail_distance_bps REAL,
    timeout_seconds REAL,
    exit_time DATETIME NOT NULL,
    exit_price REAL NOT NULL,
    exit_qty REAL NOT NULL,
    exit_reason VARCHAR(16) NOT NULL,
    pnl_usd REAL NOT NULL,
    pnl_bps REAL NOT NULL,
    pnl_percent REAL NOT NULL,
    hold_duration_sec REAL NOT NULL,
    max_favorable_excursion_bps REAL,
    max_adverse_excursion_bps REAL,
    peak_price REAL,
    lowest_price REAL,
    peak_time DATETIME,
    lowest_time DATETIME,
    optimal_tp_bps REAL,
    optimal_sl_bps REAL,
    was_trailing_beneficial INTEGER,
    could_have_won INTEGER,
    win INTEGER NOT NULL,
    hit_tp INTEGER DEFAULT 0,
    hit_sl INTEGER DEFAULT 0,
    hit_trailing INTEGER DEFAULT 0,
    timed_out INTEGER DEFAULT 0,
    strategy_tag VARCHAR(64),
    exploration_mode INTEGER DEFAULT 0,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_ml_trade_outcomes_symbol ON ml_trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_ml_trade_outcomes_entry_time ON ml_trade_outcomes(entry_time);
CREATE INDEX IF NOT EXISTS idx_ml_trade_outcomes_exit_reason ON ml_trade_outcomes(exit_reason);
CREATE INDEX IF NOT EXISTS idx_ml_trade_outcomes_win ON ml_trade_outcomes(win);
"""

cursor.executescript(sql)
conn.commit()

cursor.execute('SELECT COUNT(*) FROM ml_trade_outcomes')
print(f"✅ Таблица создана! Записей: {cursor.fetchone()[0]}")

conn.close()