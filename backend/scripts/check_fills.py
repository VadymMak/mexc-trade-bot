import sqlite3
from datetime import datetime

db = sqlite3.connect('mexc.db')
cursor = db.cursor()

print("=" * 60)
print("1. TOTAL FILLS COUNT")
print("=" * 60)
cursor.execute("SELECT COUNT(*) as total_fills FROM fills;")
print(f"Total fills: {cursor.fetchone()[0]}\n")

print("=" * 60)
print("2. ETHUSDT FILLS (Last 5)")
print("=" * 60)
cursor.execute("""
    SELECT id, symbol, side, qty, price, fee, fee_asset, executed_at 
    FROM fills 
    WHERE symbol='ETHUSDT' 
    ORDER BY id DESC 
    LIMIT 5;
""")
for row in cursor.fetchall():
    print(f"ID: {row[0]}")
    print(f"  Symbol: {row[1]}, Side: {row[2]}")
    print(f"  Qty: {row[3]}, Price: {row[4]}")
    print(f"  Fee: {row[5]} {row[6]}")
    print(f"  Executed: {row[7]}")
    print()

print("=" * 60)
print("3. ALL FEES FOR ETHUSDT")
print("=" * 60)
cursor.execute("""
    SELECT symbol, fee, fee_asset, executed_at 
    FROM fills 
    WHERE symbol='ETHUSDT';
""")
rows = cursor.fetchall()
print(f"Found {len(rows)} fills:")
for row in rows:
    print(f"  {row[0]}: fee={row[1]} {row[2]}, time={row[3]}")

print("\n" + "=" * 60)
print("4. DATABASE CURRENT TIME")
print("=" * 60)
cursor.execute("SELECT datetime('now'), date('now');")
db_time = cursor.fetchone()
print(f"Database time: {db_time[0]}")
print(f"Database date: {db_time[1]}")
print(f"Python time:   {datetime.utcnow()}")

print("\n" + "=" * 60)
print("5. EXECUTED_AT FORMAT CHECK")
print("=" * 60)
cursor.execute("""
    SELECT executed_at, typeof(executed_at) 
    FROM fills 
    ORDER BY id DESC 
    LIMIT 2;
""")
for row in cursor.fetchall():
    print(f"  executed_at: {row[0]} (type: {row[1]})")

print("\n" + "=" * 60)
print("6. SUM OF FEES")
print("=" * 60)
cursor.execute("""
    SELECT 
        symbol,
        COUNT(*) as count,
        SUM(fee) as total_fee
    FROM fills 
    WHERE symbol='ETHUSDT'
    GROUP BY symbol;
""")
row = cursor.fetchone()
if row:
    print(f"  Symbol: {row[0]}")
    print(f"  Count: {row[1]}")
    print(f"  Total fee: {row[2]}")
else:
    print("  No results found")

db.close()