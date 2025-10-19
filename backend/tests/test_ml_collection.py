"""
Тест сбора ML данных.
Запускать через: python test_ml_collection.py
"""

import sqlite3
import time
from datetime import datetime

def test_ml_snapshots():
    """Проверить что ML snapshots пишутся в БД."""
    
    print("=" * 60)
    print("ТЕСТ: ML Data Collection")
    print("=" * 60)
    
    conn = sqlite3.connect('mexc.db')
    cur = conn.cursor()
    
    # ТЕСТ 1: Проверка таблицы
    print("\n[ТЕСТ 1] Проверка существования таблицы ml_snapshots...")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_snapshots'")
    result = cur.fetchone()
    
    if result:
        print("✅ Таблица ml_snapshots существует")
    else:
        print("❌ ОШИБКА: Таблица ml_snapshots не найдена!")
        conn.close()
        return False
    
    # ТЕСТ 2: Общее количество записей
    print("\n[ТЕСТ 2] Подсчёт общего количества записей...")
    cur.execute("SELECT COUNT(*) FROM ml_snapshots")
    total = cur.fetchone()[0]
    
    print(f"   Всего записей: {total}")
    
    if total == 0:
        print("⚠️  ВНИМАНИЕ: Нет записей в БД!")
        print("   Возможные причины:")
        print("   1. Бэкенд только что запустился (подождите 30-60 сек)")
        print("   2. SYMBOLS пустой в .env")
        print("   3. ML_LOGGING_ENABLED=false")
        print("   4. BookTracker не получает данные от MEXC")
        conn.close()
        return False
    else:
        print(f"✅ Записи найдены: {total} строк")
    
    # ТЕСТ 3: Записи по символам
    print("\n[ТЕСТ 3] Распределение по символам...")
    cur.execute("""
        SELECT symbol, COUNT(*) as cnt 
        FROM ml_snapshots 
        GROUP BY symbol 
        ORDER BY cnt DESC
    """)
    
    for row in cur.fetchall():
        symbol, count = row
        print(f"   {symbol}: {count} записей")
    
    # ТЕСТ 4: Последние записи
    print("\n[ТЕСТ 4] Проверка свежести данных...")
    cur.execute("""
        SELECT symbol, ts, bid, ask, mid 
        FROM ml_snapshots 
        ORDER BY ts DESC 
        LIMIT 5
    """)
    
    print("   Последние 5 записей:")
    for row in cur.fetchall():
        symbol, ts_ms, bid, ask, mid = row
        dt = datetime.fromtimestamp(ts_ms / 1000)
        age_sec = (time.time() * 1000 - ts_ms) / 1000
        
        print(f"   {symbol} @ {dt.strftime('%H:%M:%S')} (возраст: {age_sec:.1f}s)")
        print(f"      bid={bid}, ask={ask}, mid={mid}")
    
    # Проверка возраста последней записи
    cur.execute("SELECT MAX(ts) FROM ml_snapshots")
    last_ts = cur.fetchone()[0]
    
    if last_ts:
        age_sec = (time.time() * 1000 - last_ts) / 1000
        
        if age_sec < 10:
            print(f"   ✅ Данные свежие (возраст последней записи: {age_sec:.1f}s)")
        elif age_sec < 60:
            print(f"   ⚠️  Данные старые (возраст: {age_sec:.1f}s), возможно ML Logger остановлен")
        else:
            print(f"   ❌ Данные очень старые (возраст: {age_sec:.1f}s), ML Logger точно не работает")
    
    # ТЕСТ 5: Проверка NULL значений
    print("\n[ТЕСТ 5] Проверка качества данных...")
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN bid IS NULL THEN 1 ELSE 0 END) as null_bid,
            SUM(CASE WHEN ask IS NULL THEN 1 ELSE 0 END) as null_ask,
            SUM(CASE WHEN mid IS NULL THEN 1 ELSE 0 END) as null_mid
        FROM ml_snapshots
    """)
    
    total, null_bid, null_ask, null_mid = cur.fetchone()
    
    print(f"   Всего записей: {total}")
    print(f"   NULL bid: {null_bid} ({null_bid/total*100:.1f}%)")
    print(f"   NULL ask: {null_ask} ({null_ask/total*100:.1f}%)")
    print(f"   NULL mid: {null_mid} ({null_mid/total*100:.1f}%)")
    
    if null_bid == 0 and null_ask == 0:
        print("   ✅ Нет NULL значений в bid/ask")
    else:
        print("   ⚠️  Есть NULL значения - BookTracker не получает данные корректно")
    
    # ТЕСТ 6: Частота записей
    print("\n[ТЕСТ 6] Проверка частоты записей...")
    cur.execute("""
        SELECT symbol, 
               MIN(ts) as first_ts, 
               MAX(ts) as last_ts,
               COUNT(*) as cnt
        FROM ml_snapshots
        GROUP BY symbol
    """)
    
    for row in cur.fetchall():
        symbol, first_ts, last_ts, cnt = row
        
        if first_ts and last_ts and first_ts != last_ts:
            duration_sec = (last_ts - first_ts) / 1000
            rate = cnt / duration_sec if duration_sec > 0 else 0
            
            print(f"   {symbol}:")
            print(f"      Длительность: {duration_sec:.1f}s")
            print(f"      Записей: {cnt}")
            print(f"      Частота: {rate:.2f} записей/сек (ожидается ~0.5)")
            
            if 0.4 <= rate <= 0.6:
                print(f"      ✅ Частота в норме")
            else:
                print(f"      ⚠️  Частота не соответствует ожидаемой (2s интервал)")
    
    # ТЕСТ 7: Пример данных
    print("\n[ТЕСТ 7] Пример одной записи (все поля)...")
    cur.execute("""
        SELECT * FROM ml_snapshots 
        WHERE bid IS NOT NULL 
        LIMIT 1
    """)
    
    columns = [desc[0] for desc in cur.description]
    row = cur.fetchone()
    
    if row:
        print("   Структура записи:")
        for col, val in zip(columns, row):
            if val is not None:
                print(f"      {col}: {val}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("ТЕСТ ЗАВЕРШЁН")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    test_ml_snapshots()