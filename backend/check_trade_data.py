import sqlite3
import os

# Check backend databases
db_files = [
    'mexc.db',
    'ml_trade_outcomes.db',
    'mexc_baseline_291trades.db',
    'mexc_baseline_291trades_ml.db'
]

print("Checking databases in backend folder:\n")
for db_file in db_files:
    db_path = db_file
    if os.path.exists(db_path):
        print(f"Database: {db_file}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Get all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            print(f"  Tables: {[t[0] for t in tables]}")
            
            # Check trades table if it exists
            if ('trades',) in tables:
                cursor.execute("SELECT COUNT(*) FROM trades")
                count = cursor.fetchone()[0]
                print(f"  Trades count: {count}")
                
                # Get sample of first few rows
                cursor.execute("SELECT * FROM trades LIMIT 1")
                columns = [desc[0] for desc in cursor.description]
                print(f"  Columns: {columns}")
            
            conn.close()
            print()
        except Exception as e:
            print(f"  Error: {e}\n")
    else:
        print(f"Database: {db_file} - NOT FOUND\n")

# Also check ml_collector folder
print("\nChecking ml_collector folder:")
ml_collector_dbs = [
    '../ml_collector/ml_trade_outcomes.db',
    '../ml_collector/mexc.db'
]

for db_file in ml_collector_dbs:
    if os.path.exists(db_file):
        print(f"Database: {db_file}")
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            print(f"  Tables: {[t[0] for t in tables]}")
            
            if ('trades',) in tables:
                cursor.execute("SELECT COUNT(*) FROM trades")
                count = cursor.fetchone()[0]
                print(f"  Trades count: {count}")
            
            conn.close()
            print()
        except Exception as e:
            print(f"  Error: {e}\n")
    else:
        print(f"Database: {db_file} - NOT FOUND\n")
