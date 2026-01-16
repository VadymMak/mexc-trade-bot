import sqlite3

conn = sqlite3.connect('mexc_backup_20251025.db')

tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'").fetchall()

print("=== TABLE SCHEMAS ===\n")

for (table_name,) in tables:
    print(f"-- {table_name}")
    schema = conn.execute(f"SELECT sql FROM sqlite_master WHERE name='{table_name}'").fetchone()
    if schema:
        print(schema[0])
    print()

conn.close()