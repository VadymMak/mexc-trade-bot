import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Последние 5 снапшотов
cursor.execute('''
    SELECT 
        symbol,
        created_at,
        atr1m_pct,
        grinder_ratio,
        pullback_median_retrace,
        imbalance,
        spread_bps
    FROM ml_snapshots
    ORDER BY created_at DESC
    LIMIT 5
''')

print("Последние 5 снапшотов:")
print()
for row in cursor.fetchall():
    symbol, dt, atr, grinder, pullback, imb, spread = row
    print(f"{symbol:10s} {dt}")
    atr_str = f"{atr:.2f}" if atr else "NULL"
    grinder_str = f"{grinder:.2f}" if grinder else "NULL"
    pullback_str = f"{pullback:.2f}" if pullback else "NULL"
    print(f"  ATR: {atr_str:>8}  Grinder: {grinder_str:>8}  Pullback: {pullback_str:>8}")
    print(f"  Imb: {imb:>8.3f}  Spread: {spread:>8.2f}")
    print()

# Статистика
cursor.execute('''
    SELECT 
        COUNT(*) as total,
        COUNT(atr1m_pct) as has_atr,
        COUNT(grinder_ratio) as has_grinder
    FROM ml_snapshots
''')

total, has_atr, has_grinder = cursor.fetchone()

print(f"Статистика:")
print(f"  Всего: {total}")
if total > 0:
    print(f"  С ATR: {has_atr} ({has_atr/total*100:.1f}%)")
    print(f"  С Grinder: {has_grinder} ({has_grinder/total*100:.1f}%)")

if has_atr > 0:
    print("\nОТЛИЧНО! ATR собирается!")
else:
    print("\nATR всё ещё NULL - проверьте backend")

conn.close()