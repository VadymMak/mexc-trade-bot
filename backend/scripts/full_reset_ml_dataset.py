"""
Full reset of ml_trade_outcomes for clean data collection.

WHY: Old data mixes two eras:
  - large_spread era: WR=35%, dirty signal, poisons ML training
  - zscore era: WR=95%+, clean signal, but missing ml_score column

After reset, every new record will have:
  - ml_score: what old XGBoost model thinks
  - ml_would_block: would old model have blocked this trade
  - actual outcome: profitable / net_pnl_usdt

This enables controlled retraining after 3000+ clean trades.

Usage:
  DATABASE_URL=<url> python backend/scripts/full_reset_ml_dataset.py

  # Dry run (no delete):
  DATABASE_URL=<url> python backend/scripts/full_reset_ml_dataset.py --dry-run
"""

import os
import sys
import argparse
from datetime import datetime
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("❌ DATABASE_URL env var is required")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true", help="Show what would happen, no changes")
args = parser.parse_args()

with engine.connect() as conn:
    # ── Step 1: Count before ────────────────────────────────────────
    result = conn.execute(text("""
        SELECT
            COUNT(*) as total,
            ROUND(AVG(CAST(profitable AS FLOAT))::numeric, 4) as win_rate,
            ROUND(SUM(net_pnl_usdt)::numeric, 2) as net_pnl,
            MIN(entry_time) as oldest,
            MAX(entry_time) as newest
        FROM ml_trade_outcomes;
    """)).fetchone()

    print("=" * 60)
    print("ml_trade_outcomes BEFORE:")
    print(f"  Total records : {result[0]}")
    print(f"  Win rate      : {result[1]}")
    print(f"  Total PnL     : ${result[2]}")
    print(f"  Oldest record : {result[3]}")
    print(f"  Newest record : {result[4]}")
    print("=" * 60)

    if args.dry_run:
        print("\n🔍 DRY RUN — no changes made")
        sys.exit(0)

    # ── Step 2: Backup ──────────────────────────────────────────────
    ts = datetime.now().strftime("%Y_%m_%d")
    backup_table = f"ml_trade_outcomes_backup_{ts}"

    print(f"\nCreating backup → {backup_table} ...")
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {backup_table}
        AS SELECT * FROM ml_trade_outcomes;
    """))
    conn.commit()

    backup_count = conn.execute(text(f"SELECT COUNT(*) FROM {backup_table}")).fetchone()[0]
    print(f"✅ Backup created: {backup_count} records in {backup_table}")

    # ── Step 3: Schema migration ────────────────────────────────────
    print("\nApplying schema migration (ml_score, ml_would_block columns)...")
    conn.execute(text("""
        ALTER TABLE ml_trade_outcomes
        ADD COLUMN IF NOT EXISTS ml_score FLOAT DEFAULT NULL;
    """))
    conn.execute(text("""
        ALTER TABLE ml_trade_outcomes
        ADD COLUMN IF NOT EXISTS ml_would_block BOOLEAN DEFAULT NULL;
    """))
    conn.commit()
    print("✅ Schema migration applied")

    # ── Step 4: TRUNCATE ────────────────────────────────────────────
    print("\nTruncating ml_trade_outcomes...")
    conn.execute(text("TRUNCATE TABLE ml_trade_outcomes;"))
    conn.commit()

    final_count = conn.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()[0]
    print(f"✅ Table cleared: {final_count} records remaining")

    # ── Step 5: Verify new columns present ─────────────────────────
    cols = conn.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'ml_trade_outcomes'
          AND column_name IN ('ml_score', 'ml_would_block')
        ORDER BY column_name;
    """)).fetchall()

    print("\nNew columns verified:")
    for col in cols:
        print(f"  {col[0]}: {col[1]}")

    print("\n" + "=" * 60)
    print("✅ COMPLETE — Clean dataset collection started")
    print(f"   Backup: {backup_table} ({backup_count} records preserved)")
    print(f"   Active table: 0 records, ready for clean data")
    print(f"   Next milestone: 3000+ clean zscore trades → retrain ML")
    print("=" * 60)
