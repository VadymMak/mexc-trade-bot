# scripts/check_add_day_pnl_daily.py
from __future__ import annotations

import os
import re
import sys
import sqlite3
from typing import Tuple


def _normalize_db_arg(arg: str) -> Tuple[str, str]:
    """
    Accepts:
      - plain path: ./mexc.db, C:\\path\\mexc.db, /abs/path/mexc.db
      - sqlite URLs: sqlite:///./mexc.db, sqlite:////C:/path/mexc.db
    Returns (display_path, abs_fs_path)
    """
    a = arg.strip()

    # If looks like sqlite URL, strip scheme
    if a.lower().startswith("sqlite:"):
        # Remove leading "sqlite:" and any number of slashes after it
        # sqlite:///./mexc.db  -> /./mexc.db
        # sqlite:////C:/db.sqlite -> //C:/db.sqlite
        p = re.sub(r"^sqlite:\/*", "/", a, flags=re.IGNORECASE)

        # On Windows, strip a single leading slash if path like /C:/...
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", p):
            p = p[1:]

        # If path became empty, fallback
        if not p or p == "/":
            p = "./mexc.db"

        # Collapse any duplicate slashes except protocol-like starts (already removed)
        while "//" in p and not re.match(r"^[A-Za-z]+://", p):
            p = p.replace("//", "/")

        fs_path = os.path.normpath(p)
    else:
        # Plain path
        fs_path = os.path.normpath(a if a else "./mexc.db")

    disp = fs_path
    # Make absolute for sqlite3.connect
    fs_path = os.path.abspath(fs_path)
    return disp, fs_path


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,))
    return cur.fetchone() is not None


def _column_names(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table});")
    return [row[1] for row in cur.fetchall()]


def main(db_arg: str) -> int:
    disp, path = _normalize_db_arg(db_arg)
    print(f"[check_add_day_pnl_daily] opening {disp}")

    # Ensure parent dir exists when creating new DB file
    parent = os.path.dirname(path) or "."
    if parent and not os.path.isdir(parent):
        print(f"[check_add_day_pnl_daily] creating directory: {parent}")
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()

        if not _table_exists(cur, "pnl_daily"):
            print("[check_add_day_pnl_daily] table pnl_daily not found; nothing to do.")
            return 0

        cols = _column_names(cur, "pnl_daily")
        if "day" in cols:
            print("[check_add_day_pnl_daily] column 'day' already exists; ensuring index …")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pnl_daily_day ON pnl_daily(day);")
            conn.commit()
            print("[check_add_day_pnl_daily] done.")
            return 0

        print("[check_add_day_pnl_daily] adding 'day' column to pnl_daily …")
        # Keep it TEXT and nullable for backward compatibility
        cur.execute("ALTER TABLE pnl_daily ADD COLUMN day TEXT;")
        conn.commit()

        print("[check_add_day_pnl_daily] creating index idx_pnl_daily_day …")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pnl_daily_day ON pnl_daily(day);")
        conn.commit()

        print("[check_add_day_pnl_daily] done.")
        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///./mexc.db"
    sys.exit(main(arg))
