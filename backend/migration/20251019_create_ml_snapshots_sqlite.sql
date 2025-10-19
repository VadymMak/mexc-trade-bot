-- Таблица для хранения ML снапшотов
CREATE TABLE IF NOT EXISTS ml_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    
    -- Базовые цены (из SSE)
    bid REAL,
    ask REAL,
    mid REAL,
    last REAL,
    
    -- Метрики из scanner
    spread_bps REAL,
    eff_spread_bps_maker REAL,
    depth5_bid_usd REAL,
    depth5_ask_usd REAL,
    depth10_bid_usd REAL,
    depth10_ask_usd REAL,
    imbalance REAL,
    trades_per_min REAL,
    usd_per_min REAL,
    median_trade_usd REAL,
    atr1m_pct REAL,
    grinder_ratio REAL,
    pullback_median_retrace REAL,
    spike_count_90m INTEGER,
    imbalance_sigma_hits_60m INTEGER,
    ws_lag_ms INTEGER,
    
    -- Maker-специфичные
    your_offset_bps REAL,
    spread_volatility_5min REAL,
    
    -- Outcomes (заполняются позже)
    filled_20s BOOLEAN DEFAULT NULL,
    fill_time_s REAL DEFAULT NULL,
    mid_at_fill REAL DEFAULT NULL,
    mid_at_20s REAL DEFAULT NULL,
    profit_bps REAL DEFAULT NULL,
    exit_spread_bps REAL DEFAULT NULL,
    
    -- Метаданные
    scanner_preset TEXT,
    ml_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы (с проверкой существования через DROP IF EXISTS + CREATE)
DROP INDEX IF EXISTS idx_ml_snapshots_symbol_ts;
CREATE INDEX idx_ml_snapshots_symbol_ts ON ml_snapshots(symbol, ts);

DROP INDEX IF EXISTS idx_ml_snapshots_outcomes;
CREATE INDEX idx_ml_snapshots_outcomes ON ml_snapshots(filled_20s, profit_bps) WHERE filled_20s IS NOT NULL;

DROP INDEX IF EXISTS idx_ml_snapshots_exchange;
CREATE INDEX idx_ml_snapshots_exchange ON ml_snapshots(exchange);

DROP INDEX IF EXISTS idx_ml_snapshots_ts;
CREATE INDEX idx_ml_snapshots_ts ON ml_snapshots(ts);