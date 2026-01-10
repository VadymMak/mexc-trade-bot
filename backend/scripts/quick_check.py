"""
HFT Stats - LAST 3 HOURS ONLY
Filters by recent data only!
"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Calculate 3 hours ago timestamp
now = datetime.utcnow()
three_hours_ago = now - timedelta(hours=3)
cutoff_time = three_hours_ago.strftime('%Y-%m-%d %H:%M:%S')

print("=" * 80)
print("📊 HFT TEST RESULTS - LAST 3 HOURS ONLY")
print("=" * 80)
print(f"Cutoff time: {cutoff_time}")
print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print()

# 1. Recent CLOSED positions (last 3 hours)
print("1️⃣ RECENT CLOSED POSITIONS (Last 3 hours):")
print("-" * 80)

cursor.execute("""
    SELECT COUNT(*) 
    FROM positions
    WHERE workspace_id = 1 
      AND status = 'CLOSED'
      AND updated_at >= ?
""", (cutoff_time,))

recent_closed = cursor.fetchone()[0]
print(f"Total closed positions (last 3h): {recent_closed}")

if recent_closed == 0:
    print("\n⚠️ NO RECENT CLOSED POSITIONS!")
    print("Checking if test is actually running...")
    
    # Check if there are ANY recent positions (open or closed)
    cursor.execute("""
        SELECT COUNT(*) 
        FROM positions
        WHERE workspace_id = 1 
          AND created_at >= ?
    """, (cutoff_time,))
    
    recent_any = cursor.fetchone()[0]
    print(f"Total positions created (last 3h): {recent_any}")
    
    if recent_any == 0:
        print("\n❌ NO ACTIVITY IN LAST 3 HOURS!")
        print("Test may have stopped or not running!")
        
        # Check last activity
        cursor.execute("""
            SELECT MAX(created_at) 
            FROM positions
            WHERE workspace_id = 1
        """)
        last_activity = cursor.fetchone()[0]
        print(f"\nLast position created: {last_activity}")
    else:
        print(f"\n✅ Test IS running! Created {recent_any} positions in last 3h")
        
        # Check if they're still open (waiting to close)
        cursor.execute("""
            SELECT symbol, COUNT(*) as count
            FROM positions
            WHERE workspace_id = 1 
              AND status = 'OPEN'
              AND created_at >= ?
            GROUP BY symbol
        """, (cutoff_time,))
        
        print("\nCurrently OPEN positions (from last 3h):")
        for symbol, count in cursor.fetchall():
            print(f"  {symbol:12} | {count} open")

else:
    # Per symbol breakdown (last 3h)
    cursor.execute("""
        SELECT 
            symbol,
            COUNT(*) as closed_count
        FROM positions
        WHERE workspace_id = 1 
          AND status = 'CLOSED'
          AND updated_at >= ?
        GROUP BY symbol
        ORDER BY closed_count DESC
    """, (cutoff_time,))
    
    print("\nPer Symbol (last 3h):")
    for symbol, count in cursor.fetchall():
        print(f"  {symbol:12} | {count:4} closed")
    
    # Calculate frequency
    print(f"\nFREQUENCY: {recent_closed} trades / 3 hours = {recent_closed/3:.1f} trades/hour")
    
print()

# 2. Currently OPEN positions
print("2️⃣ CURRENTLY OPEN POSITIONS:")
print("-" * 80)

cursor.execute("""
    SELECT 
        symbol,
        id,
        created_at,
        CAST((julianday('now') - julianday(created_at)) * 86400 AS REAL) as age_sec
    FROM positions
    WHERE workspace_id = 1 AND status = 'OPEN'
    ORDER BY symbol, created_at
""")

open_positions = cursor.fetchall()
print(f"Total OPEN: {len(open_positions)}")
print()

if open_positions:
    print("Symbol       | Position ID | Created At          | Age (sec)")
    print("-" * 80)
    for symbol, pos_id, created, age in open_positions:
        print(f"{symbol:12} | {pos_id:11} | {created:19} | {age:9.1f}")
else:
    print("No open positions!")

print()

# 3. Recent orders (last 3 hours)
print("3️⃣ RECENT ORDERS (Last 3 hours):")
print("-" * 80)

cursor.execute("""
    SELECT COUNT(*)
    FROM orders
    WHERE workspace_id = 1
      AND created_at >= ?
""", (cutoff_time,))

recent_orders = cursor.fetchone()[0]
print(f"Orders created (last 3h): {recent_orders}")

if recent_orders > 0:
    cursor.execute("""
        SELECT 
            symbol,
            side,
            COUNT(*) as count
        FROM orders
        WHERE workspace_id = 1
          AND created_at >= ?
        GROUP BY symbol, side
    """, (cutoff_time,))
    
    print("\nPer Symbol & Side:")
    for symbol, side, count in cursor.fetchall():
        print(f"  {symbol:12} {side:4} | {count:4} orders")

print()

# 4. Test timeline
print("4️⃣ ACTUAL TEST TIMELINE:")
print("-" * 80)

cursor.execute("""
    SELECT 
        MIN(created_at) as first,
        MAX(created_at) as last,
        MAX(updated_at) as last_update
    FROM positions
    WHERE workspace_id = 1
""")

first, last, last_update = cursor.fetchone()
print(f"First position ever: {first}")
print(f"Last position created: {last}")
print(f"Last position updated: {last_update}")

if last:
    try:
        last_dt = datetime.fromisoformat(last.replace('Z', '').replace(' ', 'T'))
        now_dt = datetime.utcnow()
        time_since_last = (now_dt - last_dt).total_seconds() / 60
        
        print(f"\n⏰ Time since last position: {time_since_last:.1f} minutes")
        
        if time_since_last > 5:
            print("⚠️ WARNING: No new positions in last 5+ minutes!")
            print("Test may have stopped or stalled!")
        else:
            print("✅ Test appears to be running (recent activity)")
    except Exception as e:
        print(f"(Could not parse timestamp: {e})")

print()

# 5. Quick summary
print("=" * 80)
print("📋 SUMMARY:")
print("-" * 80)

cursor.execute("SELECT COUNT(*) FROM positions WHERE workspace_id = 1 AND status = 'OPEN'")
total_open = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM positions WHERE workspace_id = 1 AND status = 'CLOSED'")
total_closed = cursor.fetchone()[0]

print(f"Total OPEN positions: {total_open}")
print(f"Total CLOSED positions: {total_closed}")
print(f"Recent closed (last 3h): {recent_closed}")

if recent_closed > 0:
    freq_3h = recent_closed / 3.0
    print(f"\n📊 Frequency (last 3h): {freq_3h:.1f} trades/hour")
    
    if freq_3h < 50:
        print("⚠️ LOW FREQUENCY! Expected: 500+ trades/hour")
        print("   Actual is ~10x slower than target!")
    elif freq_3h < 200:
        print("⚠️ MEDIUM FREQUENCY. Better, but still below target.")
    else:
        print("✅ GOOD FREQUENCY!")

print()
print("=" * 80)

conn.close()