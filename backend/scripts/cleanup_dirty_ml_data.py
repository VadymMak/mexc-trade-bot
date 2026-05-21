"""
Remove dirty training data from ml_trade_outcomes.
Dirty data = large_spread era (WR 35%) that poisons ML training.

IMPORTANT: Run backup first, then cleanup, then verify count.
Expected result: ~6300 records remaining, WR ~91%, PnL ~$370
"""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("Creating backup...")
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ml_trade_outcomes_backup_pre_cleanup
        AS SELECT * FROM ml_trade_outcomes;
    """))
    conn.commit()

    result = conn.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()
    print(f"Before cleanup: {result[0]} records")

    deleted = conn.execute(text("""
        DELETE FROM ml_trade_outcomes
        WHERE entry_mode = 'large_spread'
           OR hold_seconds < 10
           OR hold_seconds > 3600
           OR exit_reason IS NULL
           OR entry_spread_pct < 0.3
           OR entry_spread_pct > 10;
    """))
    conn.commit()
    print(f"Deleted: {deleted.rowcount} dirty records")

    result = conn.execute(text("""
        SELECT
            COUNT(*) as remaining,
            ROUND(AVG(CAST(profitable AS FLOAT))::numeric, 4) as win_rate,
            ROUND(SUM(net_pnl_usdt)::numeric, 2) as net_pnl
        FROM ml_trade_outcomes;
    """)).fetchone()
    print(f"After cleanup: {result[0]} records, WR={result[1]}, PnL=${result[2]}")
    print("✅ Cleanup complete. Run add_ml_score_columns.py next to add ml_score columns.")
