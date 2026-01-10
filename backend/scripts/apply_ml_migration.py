import sqlite3

db_path = "mexc.db"
migration_file = "migration/20251106_create_ml_trade_outcomes.sql"

print("=" * 70)
print("ПРИМЕНЕНИЕ МИГРАЦИИ: ml_trade_outcomes")
print("=" * 70)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Читаем SQL
with open(migration_file, 'r', encoding='utf-8') as f:
    sql = f.read()

try:
    # Применяем миграцию
    cursor.executescript(sql)
    conn.commit()
    
    # Проверяем
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_trade_outcomes';")
    result = cursor.fetchone()
    
    if result:
        print("✅ Таблица ml_trade_outcomes успешно создана!\n")
        
        # Показываем структуру
        cursor.execute('PRAGMA table_info(ml_trade_outcomes)')
        columns = cursor.fetchall()
        
        print("📋 СТРУКТУРА ТАБЛИЦЫ:")
        print("-" * 70)
        for col in columns:
            col_id, name, type_, notnull, default, pk = col
            pk_mark = '🔑 PK' if pk else ''
            req = '(required)' if notnull and not pk else ''
            print(f"  {name:35s} {type_:15s} {pk_mark} {req}")
        
        print("\n" + "=" * 70)
        print("ГОТОВО! Таблица готова к использованию!")
        print("=" * 70)
    else:
        print("❌ Ошибка: таблица не создана")
        
except Exception as e:
    print(f"❌ ОШИБКА при применении миграции: {e}")
    conn.rollback()

conn.close()