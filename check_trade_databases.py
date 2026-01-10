import sqlite3
import os

# Check backend databases
db_files = [
    'backend/mexc.db',
    'backend/mexc_backup_20251025.db',
    'backend/mexc_backup_baseline_2025-10-30.db',
    'backend/mexc_before_ab_test.db'
]

print("=" * 80)
print("CHECKING TRADE DATABASES")
print("=" * 80)

for db_path in db_files:
    if os.path.exists(db_path):
        db_name = os.path.basename(db_path)
        file_size_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"\n📊 Database: {db_name} ({file_size_mb:.2f} MB)")
        print("-" * 80)
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Get all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            print(f"  Tables: {', '.join(tables)}")
            
            # Check trades table if it exists
            if 'trades' in tables:
                cursor.execute("SELECT COUNT(*) FROM trades")
                count = cursor.fetchone()[0]
                print(f"  ✓ TRADES COUNT: {count:,}")
                
                # Get column info
                cursor.execute("PRAGMA table_info(trades)")
                columns = [col[1] for col in cursor.fetchall()]
                print(f"  Columns ({len(columns)}): {', '.join(columns[:10])}...")
                
                # Get date range if timestamp column exists
                if 'timestamp' in columns or 'created_at' in columns or 'time' in columns:
                    time_col = 'timestamp' if 'timestamp' in columns else ('created_at' if 'created_at' in columns else 'time')
                    try:
                        cursor.execute(f"SELECT MIN({time_col}), MAX({time_col}) FROM trades")
                        min_time, max_time = cursor.fetchone()
                        print(f"  Time range: {min_time} to {max_time}")
                    except:
                        pass
                
                # Check outcomes distribution
                if 'outcome' in columns:
                    cursor.execute("SELECT outcome, COUNT(*) FROM trades GROUP BY outcome")
                    outcomes = cursor.fetchall()
                    print(f"  Outcomes distribution:")
                    for outcome, cnt in outcomes:
                        print(f"    - {outcome}: {cnt:,}")
            else:
                print(f"  ⚠ No 'trades' table found")
            
            # Check fills table if it exists
            if 'fills' in tables:
                cursor.execute("SELECT COUNT(*) FROM fills")
                fills_count = cursor.fetchone()[0]
                print(f"  ✓ FILLS COUNT: {fills_count:,}")
            
            # Check ml_snapshots table if it exists
            if 'ml_snapshots' in tables:
                cursor.execute("SELECT COUNT(*) FROM ml_snapshots")
                ml_count = cursor.fetchone()[0]
                print(f"  ✓ ML_SNAPSHOTS COUNT: {ml_count:,}")
            
            conn.close()
            
        except Exception as e:
            print(f"  ❌ Error: {e}")

print("\n" + "=" * 80)
print("SUMMARY - DATABASES WITH 50,000+ TRADES:")
print("=" * 80)

# Summary of databases with large trade counts
for db_path in db_files:
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            if cursor.fetchone():
                cursor.execute("SELECT COUNT(*) FROM trades")
                count = cursor.fetchone()[0]
                if count >= 50000:
                    db_name = os.path.basename(db_path)
                    print(f"✓ {db_name}: {count:,} trades")
            conn.close()
        except:
            pass

print("=" * 80)
