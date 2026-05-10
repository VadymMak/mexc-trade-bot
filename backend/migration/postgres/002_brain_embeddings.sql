-- =============================================
-- BRAIN EMBEDDINGS (pgvector)
-- Semantic memory for trade pattern recognition
-- =============================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS brain_embeddings (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT,
    session         TEXT,           -- 'asia' | 'europe' | 'us'
    hour_utc        INTEGER,
    day_of_week     INTEGER,        -- 0=Mon … 6=Sun
    is_weekend      BOOLEAN,
    entry_mode      TEXT,           -- 'zscore' | 'scanner' | 'mm' …
    entry_spread_pct FLOAT,
    entry_zscore    FLOAT,
    spread_mean     FLOAT,
    spread_std      FLOAT,
    buy_pressure    FLOAT,
    trade_velocity  FLOAT,
    book_imbalance  FLOAT,
    mins_to_funding FLOAT,
    exit_reason     TEXT,           -- 'TP' | 'SL' | 'TIMEOUT' | 'TRAIL'
    hold_seconds    INTEGER,
    pnl_pct         FLOAT,
    net_pnl_usdt    FLOAT,
    profitable      BOOLEAN,
    scan_embedding  vector(1536),   -- OpenAI text-embedding-3-small
    created_at      TIMESTAMP DEFAULT NOW()
);

-- IVFFlat cosine index (rebuild after load: CREATE INDEX CONCURRENTLY)
CREATE INDEX IF NOT EXISTS brain_embedding_idx
    ON brain_embeddings
    USING ivfflat (scan_embedding vector_cosine_ops)
    WITH (lists = 100);

-- Auxiliary indexes for analytical queries
CREATE INDEX IF NOT EXISTS brain_profitable_idx ON brain_embeddings (profitable);
CREATE INDEX IF NOT EXISTS brain_session_idx    ON brain_embeddings (session);
CREATE INDEX IF NOT EXISTS brain_hour_idx       ON brain_embeddings (hour_utc);
CREATE INDEX IF NOT EXISTS brain_symbol_idx     ON brain_embeddings (symbol);
