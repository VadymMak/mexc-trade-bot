import sqlite3

conn = sqlite3.connect('mexc.db')
cursor = conn.cursor()

print("\n" + "="*60)
print("POSITIONS TABLE SCHEMA")
print("="*60)

cursor.execute("PRAGMA table_info(positions)")
columns = cursor.fetchall()

print("\nColumns:")
for col in columns:
    print(f"  {col[1]:20s} {col[2]:10s}")

conn.close()