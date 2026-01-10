import sqlite3
import json

def check_db_structure(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all tables
    tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    
    print("\n" + "="*60)
    print(f"DATABASE: {db_path}")
    print("="*60)
    print(f"\nNumber of tables: {len(tables)}\n")
    
    for table in tables:
        table_name = table[0]
        print(f"\n{'='*60}")
        print(f"TABLE: {table_name}")
        print('='*60)
        
        # Get column info
        columns = cursor.execute(f'PRAGMA table_info({table_name})').fetchall()
        print(f"\nColumns ({len(columns)}):")
        for col in columns:
            col_id, col_name, col_type, not_null, default_val, pk = col
            pk_marker = " [PRIMARY KEY]" if pk else ""
            not_null_marker = " NOT NULL" if not_null else ""
            print(f"  - {col_name} ({col_type}){pk_marker}{not_null_marker}")
        
        # Get row count
        row_count = cursor.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]
        print(f"\nRow count: {row_count}")
        
        # Get sample data
        if row_count > 0:
            print(f"\nSample data (first 5 rows):")
            sample_data = cursor.execute(f'SELECT * FROM {table_name} LIMIT 5').fetchall()
            col_names = [col[1] for col in columns]
            print(f"  Columns: {col_names}\n")
            for i, row in enumerate(sample_data, 1):
                print(f"  Row {i}:")
                for col_name, value in zip(col_names, row):
                    print(f"    {col_name}: {value}")
                print()
        else:
            print("\nNo data in table")
    
    conn.close()

if __name__ == "__main__":
    check_db_structure('ml_trade_outcomes.db')
