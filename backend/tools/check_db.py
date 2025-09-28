# File: tools/check_db.py
import sqlite3
from pathlib import Path

# Use the same DB path as in your .env
db_path = Path("C:/projects/mexc-trade-bot/backend/mexc.db")
print(f"Using database: {db_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

print("\n--- Tables ---")
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
print([t[0] for t in tables])

print("\n--- Distinct symbols in orders ---")
print(cur.execute("SELECT DISTINCT symbol FROM orders").fetchall())

print("\n--- Orders with SOL ---")
for row in cur.execute("SELECT * FROM orders WHERE symbol LIKE '%SOL%';").fetchall():
    print(row)

print("\n--- Orders with ETH ---")
for row in cur.execute("SELECT * FROM orders WHERE symbol LIKE '%ETH%';").fetchall():
    print(row)

print("\n--- Fills with SOL ---")
try:
    for row in cur.execute("SELECT * FROM fills WHERE symbol LIKE '%SOL%';").fetchall():
        print(row)
except Exception as e:
    print("No fills table:", e)

print("\n--- Fills with ETH ---")
try:
    for row in cur.execute("SELECT * FROM fills WHERE symbol LIKE '%ETH%';").fetchall():
        print(row)
except Exception as e:
    print("No fills table:", e)

conn.close()
