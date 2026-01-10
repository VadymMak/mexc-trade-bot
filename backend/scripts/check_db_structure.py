# scripts/check_db_structure.py

import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Get all column names from trades table
cursor.execute("PRAGMA table_info(trades)")
columns = cursor.fetchall()

print("="*60)
print("TRADES TABLE STRUCTURE")
print("="*60)
print(f"Total columns: {len(columns)}\n")

print("Column list:")
for col in columns:
    col_id, name, type_, not_null, default, pk = col
    print(f"{col_id:3d}. {name:40s} {type_:15s}")

conn.close()