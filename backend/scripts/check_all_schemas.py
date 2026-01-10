# scripts/check_all_schemas.py
import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("=" * 80)
print("ВСЕ ТАБЛИЦЫ В БАЗЕ ДАННЫХ:")
print("=" * 80)

# Получить список всех таблиц
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = cursor.fetchall()

for table in tables:
    print(f"  📋 {table[0]}")

print("\n" + "=" * 80)
print("СХЕМА: ml_snapshots")
print("=" * 80)

cursor.execute('PRAGMA table_info(ml_snapshots)')
columns = cursor.fetchall()
for col in columns:
    col_id, name, type_, notnull, default, pk = col
    pk_mark = '🔑 PK' if pk else ''
    print(f"  {name:30s} {type_:15s} {pk_mark}")

print("\n" + "=" * 80)
print("СХЕМА: trades (если существует)")
print("=" * 80)

try:
    cursor.execute('PRAGMA table_info(trades)')
    trades_columns = cursor.fetchall()
    
    if trades_columns:
        for col in trades_columns:
            col_id, name, type_, notnull, default, pk = col
            pk_mark = '🔑 PK' if pk else ''
            print(f"  {name:30s} {type_:15s} {pk_mark}")
        
        print("\n📊 SAMPLE DATA (последние 3 записи):")
        cursor.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 3')
        rows = cursor.fetchall()
        print(f"  Всего записей: {len(rows)}")
        
    else:
        print("  ❌ Таблица 'trades' не найдена")
        
except Exception as e:
    print(f"  ❌ Таблица 'trades' не существует: {e}")

print("\n" + "=" * 80)
print("СХЕМА: fills (если существует)")
print("=" * 80)

try:
    cursor.execute('PRAGMA table_info(fills)')
    fills_columns = cursor.fetchall()
    
    if fills_columns:
        for col in fills_columns:
            col_id, name, type_, notnull, default, pk = col
            pk_mark = '🔑 PK' if pk else ''
            print(f"  {name:30s} {type_:15s} {pk_mark}")
        
        print("\n📊 SAMPLE DATA (последние 3 записи):")
        cursor.execute('SELECT id, symbol, side, quantity, price, created_at FROM fills ORDER BY id DESC LIMIT 3')
        rows = cursor.fetchall()
        for row in rows:
            print(f"  {row}")
            
    else:
        print("  ❌ Таблица 'fills' не найдена")
        
except Exception as e:
    print(f"  ❌ Таблица 'fills' не существует: {e}")

print("\n" + "=" * 80)
print("ПРОВЕРКА КЛЮЧЕВЫХ КОЛОНОК для ML:")
print("=" * 80)

# ml_snapshots validation
cursor.execute('PRAGMA table_info(ml_snapshots)')
ml_columns = cursor.fetchall()
ml_col_names = [c[1] for c in ml_columns]

required_ml = ['imbalance', 'depth5_bid_usd', 'depth5_ask_usd', 'spread_bps', 
               'atr1m_pct', 'grinder_ratio', 'trades_per_min', 'usd_per_min']

print("\nml_snapshots:")
for col in required_ml:
    status = "✅" if col in ml_col_names else "❌"
    print(f"  {status} {col}")

conn.close()

print("\n" + "=" * 80)
print("ГОТОВО! ✅")
print("=" * 80)