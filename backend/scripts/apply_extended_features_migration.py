"""
Apply extended features migration to ml_trade_outcomes table
"""
import sqlite3
import sys
from pathlib import Path

# Get backend directory
backend_dir = Path(__file__).parent.parent
db_path = backend_dir / "mexc.db"
migration_path = backend_dir / "migration" / "20251112_add_extended_features_sqlite.sql"

print(f"📁 Database: {db_path}")
print(f"📄 Migration: {migration_path}")

if not db_path.exists():
    print(f"❌ Database not found: {db_path}")
    sys.exit(1)

if not migration_path.exists():
    print(f"❌ Migration file not found: {migration_path}")
    sys.exit(1)

# Read migration SQL
with open(migration_path, 'r') as f:
    migration_sql = f.read()

# Apply migration
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    # Split by semicolons and execute each statement
    statements = [s.strip() for s in migration_sql.split(';') if s.strip() and not s.strip().startswith('--')]
    
    print(f"\n🔧 Applying {len(statements)} ALTER TABLE statements...")
    
    for i, statement in enumerate(statements, 1):
        if statement:
            try:
                cursor.execute(statement)
                print(f"  ✅ {i}/{len(statements)}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e):
                    print(f"  ⚠️  {i}/{len(statements)} - Column already exists (skipping)")
                else:
                    raise
    
    conn.commit()
    print("\n✅ Migration applied successfully!")
    
    # Verify columns were added
    cursor.execute("PRAGMA table_info(ml_trade_outcomes)")
    columns = cursor.fetchall()
    
    new_columns = [
        'spread_pct_entry', 'spread_abs_entry', 'eff_spread_pct_entry',
        'eff_spread_abs_entry', 'eff_spread_maker_bps_entry', 'eff_spread_taker_bps_entry',
        'base_volume_24h_entry', 'quote_volume_24h_entry',
        'maker_fee_entry', 'taker_fee_entry', 'zero_fee_entry',
        'spike_count_90m_entry', 'range_stable_pct_entry',
        'vol_pattern_entry', 'dca_potential_entry',
        'scanner_score_entry', 'ws_lag_ms_entry',
        'depth_imbalance_entry', 'depth5_total_usd_entry', 'depth10_total_usd_entry',
        'depth_ratio_5_to_10_entry', 'spread_to_depth5_ratio_entry',
        'volume_to_depth_ratio_entry', 'trades_per_dollar_entry',
        'avg_trade_size_entry', 'mid_price_entry', 'price_precision_entry'
    ]
    
    column_names = [col[1] for col in columns]
    found = sum(1 for nc in new_columns if nc in column_names)
    
    print(f"\n📊 Column verification:")
    print(f"   Total columns: {len(columns)}")
    print(f"   New columns found: {found}/{len(new_columns)}")
    
    if found == len(new_columns):
        print("   ✅ All new columns present!")
    else:
        missing = [nc for nc in new_columns if nc not in column_names]
        print(f"   ⚠️  Missing columns: {missing}")

except Exception as e:
    conn.rollback()
    print(f"\n❌ Migration failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

finally:
    conn.close()

print("\n✅ Done!")
