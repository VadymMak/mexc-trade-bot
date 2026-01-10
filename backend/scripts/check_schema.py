import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("=" * 60)
print("ML_SNAPSHOTS SCHEMA:")
print("=" * 60)

cursor.execute('PRAGMA table_info(ml_snapshots)')
columns = cursor.fetchall()

for col in columns:
    col_id, name, type_, notnull, default, pk = col
    print(f"{name:25s} {type_:15s} {'PK' if pk else ''}")

print("=" * 60)

# Check if key columns exist
has_imbalance = any(c[1] == 'imbalance' for c in columns)
has_depth_bid = any(c[1] == 'depth5_bid_usd' for c in columns)
has_depth_ask = any(c[1] == 'depth5_ask_usd' for c in columns)
has_spread = any(c[1] == 'spread_bps' for c in columns)

print(f"\n✅ Has imbalance:       {has_imbalance}")
print(f"✅ Has depth5_bid_usd:  {has_depth_bid}")
print(f"✅ Has depth5_ask_usd:  {has_depth_ask}")
print(f"✅ Has spread_bps:      {has_spread}")

if all([has_imbalance, has_depth_bid, has_depth_ask, has_spread]):
    print("\n🎉 ALL REQUIRED COLUMNS EXIST - Ready to collect!")
else:
    print("\n⚠️  MISSING COLUMNS - Need migration")

conn.close()