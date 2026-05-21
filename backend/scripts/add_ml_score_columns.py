"""Add ml_score and ml_would_block columns to ml_trade_outcomes."""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE ml_trade_outcomes
        ADD COLUMN IF NOT EXISTS ml_score FLOAT DEFAULT NULL;
    """))
    conn.execute(text("""
        ALTER TABLE ml_trade_outcomes
        ADD COLUMN IF NOT EXISTS ml_would_block BOOLEAN DEFAULT NULL;
    """))
    conn.commit()
    print("✅ Columns added: ml_score, ml_would_block")
