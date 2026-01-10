import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

# Count records
cursor.execute('SELECT COUNT(*) FROM ml_trade_outcomes')
count = cursor.fetchone()[0]

print("=" * 70)
print(f"📊 ML TRADE OUTCOMES: {count} записей")
print("=" * 70)

if count > 0:
    # Show all trades
    cursor.execute('''
        SELECT 
            trade_id, symbol, entry_time, exit_time,
            pnl_bps, exit_reason,
            max_favorable_excursion_bps, max_adverse_excursion_bps,
            hold_duration_sec
        FROM ml_trade_outcomes
        ORDER BY id DESC
    ''')
    
    print("\n📋 ВСЕ СДЕЛКИ:")
    print("-" * 70)
    
    for row in cursor.fetchall():
        trade_id, symbol, entry, exit, pnl, reason, mfe, mae, duration = row
        print(f"\n🔹 {trade_id} | {symbol}")
        print(f"   Entry:    {entry}")
        print(f"   Exit:     {exit} ({reason})")
        print(f"   P&L:      {pnl:.2f} bps")
        print(f"   MFE/MAE:  {mfe:.2f} / {mae:.2f} bps")
        print(f"   Duration: {duration:.1f}s")
else:
    print("\n❌ НЕТ ЗАПИСЕЙ В ml_trade_outcomes!")
    print("\n🔍 ВОЗМОЖНЫЕ ПРИЧИНЫ:")
    print("   1. Код stop_tracking() не выполняется при EXIT")
    print("   2. Exception в tracker.stop_tracking()")
    print("   3. Неправильный путь к БД")
    
    # Check if trade_id is stored in strategy state
    print("\n🔍 ПРОВЕРКА: Проверяем таблицу trades (старая)")
    cursor.execute('SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL')
    trades_count = cursor.fetchone()[0]
    print(f"   Закрытых сделок в 'trades': {trades_count}")
    
    if trades_count > 0:
        print("   ✅ Сделки закрываются, но ml_trade_outcomes НЕ пишется")
        print("   → Проблема в коде stop_tracking()")

conn.close()