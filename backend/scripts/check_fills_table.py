import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*70)
print("FILLS TABLE DIAGNOSIS")
print("="*70)

# Проверяем есть ли fills таблица
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fills'")
if not cursor.fetchone():
    print("\n❌ FILLS TABLE DOES NOT EXIST!")
    print("Position tracker has no data!")
else:
    print("\n✅ Fills table exists")
    
    # Количество fills
    cursor.execute("SELECT COUNT(*) FROM fills")
    fills_count = cursor.fetchone()[0]
    print(f"   Total fills: {fills_count:,}")
    
    # По символам
    cursor.execute("""
        SELECT symbol, COUNT(*) as cnt
        FROM fills
        GROUP BY symbol
        ORDER BY cnt DESC
    """)
    
    print("\n   Fills by symbol:")
    for row in cursor.fetchall():
        print(f"     {row[0]:10s} {row[1]:,}")
    
    # Сравним с trades
    cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_reason IS NOT NULL")
    trades_count = cursor.fetchone()[0]
    
    print(f"\n📊 Comparison:")
    print(f"   Trades (completed): {trades_count:,}")
    print(f"   Fills:              {fills_count:,}")
    print(f"   Ratio:              {fills_count/max(trades_count,1):.2f}")
    
    if fills_count == 0:
        print("\n❌ FILLS TABLE IS EMPTY!")
        print("   This is why Position tracker shows wrong data!")
    elif fills_count < trades_count * 0.9:
        print("\n⚠️  FILLS TABLE IS INCOMPLETE!")
        print("   Missing many fills!")

print("\n" + "="*70)
conn.close()