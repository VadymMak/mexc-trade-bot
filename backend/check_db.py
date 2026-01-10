# Создай файл check_db.py:
import sqlite3

conn = sqlite3.connect("slot_laboratory.db")
cursor = conn.cursor()

# Проверь таблицы
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print(f"Tables: {tables}")

# Проверь записи
cursor.execute("SELECT COUNT(*) FROM ml_trade_outcomes")
count = cursor.fetchone()[0]
print(f"Total records: {count}")

# Проверь последние записи
cursor.execute("SELECT trade_id, symbol, entry_time FROM ml_trade_outcomes LIMIT 5")
records = cursor.fetchall()
print(f"Records: {records}")

conn.close()