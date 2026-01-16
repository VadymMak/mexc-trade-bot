import os
import psycopg2
from pathlib import Path

DATABASE_URL = os.environ.get('DATABASE_URL')

def apply_migrations():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    # Создать таблицу для отслеживания миграций
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(255) NOT NULL UNIQUE,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Получить уже примененные
    cur.execute("SELECT filename FROM _migrations")
    applied = {row[0] for row in cur.fetchall()}
    
    # Применить новые
    migrations_dir = Path(__file__).parent / 'postgres'
    for sql_file in sorted(migrations_dir.glob('*.sql')):
        if sql_file.name not in applied:
            print(f"Applying {sql_file.name}...")
            cur.execute(sql_file.read_text())
            cur.execute(
                "INSERT INTO _migrations (filename) VALUES (%s)",
                (sql_file.name,)
            )
            print(f"  ✅ Done")
    
    conn.close()
    print("All migrations applied!")

if __name__ == '__main__':
    apply_migrations()