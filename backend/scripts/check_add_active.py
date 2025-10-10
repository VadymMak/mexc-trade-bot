import sqlite3
import os
import sys
from urllib.parse import urlparse

def parse_sqlite_path(url: str) -> str:
    # supports sqlite:///./file.db and sqlite:///C:/path/file.db
    if not url.startswith("sqlite"):
        return url  # assume plain path
    u = urlparse(url)
    p = u.path
    if p.startswith("/") and ":" in p[1:3]:
        # windows drive 'C:'
        p = p[1:]
    return p or "./app.db"

def has_column(conn, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table});")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())

def main():
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./app.db")
    if len(sys.argv) > 1:
        db_url = sys.argv[1]
    db_path = parse_sqlite_path(db_url)

    print(f"[check_add_active] opening {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        # ensure table exists (create_all usually did this already)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ui_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

        if has_column(conn, "ui_state", "active"):
            print("[check_add_active] 'active' column already present — nothing to do.")
            return

        print("[check_add_active] adding 'active' column to ui_state …")
        conn.execute("ALTER TABLE ui_state ADD COLUMN active BOOLEAN NOT NULL DEFAULT FALSE;")
        conn.commit()
        print("[check_add_active] done.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
