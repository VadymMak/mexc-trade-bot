-- brain/schema.sql
-- Neon pgvector schema для brain-vectors проекта
-- Запуск: psql $BRAIN_NEON_URL < brain/schema.sql

-- Включи pgvector расширение (Neon поддерживает нативно)
CREATE EXTENSION IF NOT EXISTS vector;

-- Таблица embeddings для trade validation
CREATE TABLE IF NOT EXISTS trade_embeddings (
    id            BIGSERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL,
    exchange_long TEXT NOT NULL,
    exchange_short TEXT NOT NULL,
    embedding     vector(1536),        -- OpenAI text-embedding-3-small
    metadata      JSONB,               -- spread_pct, zscore, outcome, etc.
    outcome       TEXT,                -- 'win' / 'loss' / 'unknown'
    net_pnl       NUMERIC(12,6),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_te_symbol
    ON trade_embeddings(symbol);
CREATE INDEX IF NOT EXISTS idx_te_outcome
    ON trade_embeddings(outcome);
-- Векторный индекс для similarity search
CREATE INDEX IF NOT EXISTS idx_te_embedding
    ON trade_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Таблица RAG контекстов
CREATE TABLE IF NOT EXISTS rag_contexts (
    id          BIGSERIAL PRIMARY KEY,
    context_key TEXT NOT NULL UNIQUE,  -- e.g. "gate|mexc|STORJ_USDT"
    content     TEXT NOT NULL,         -- текстовый контекст для RAG
    embedding   vector(1536),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rc_key
    ON rag_contexts(context_key);
CREATE INDEX IF NOT EXISTS idx_rc_embedding
    ON rag_contexts USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
