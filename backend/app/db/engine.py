# app/db/engine.py
from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# По умолчанию — локальная SQLite; можно переопределить через .env/переменные
# Примеры:
#   DATABASE_URL=sqlite:///./mexc.db
#   DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/mexc
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mexc.db").strip()

# Для SQLite нужно разрешить доступ из разных потоков (FastAPI)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # активная проверка соединения из пула
    connect_args=connect_args,
)


def apply_migrations(engine: Engine) -> None:
    """
    Применяет SQL-миграции из папки migration/ (только для SQLite).
    Сортирует файлы по имени (по дате), пропускает ошибки для idempotency.
    Вызывайте в startup event FastAPI после Base.metadata.create_all().
    """
    if not DATABASE_URL.startswith("sqlite"):
        print("ℹ️ Миграции пропущены: не SQLite DB")
        return

    migration_dir = Path(__file__).parent.parent.parent / "migration"
    if not migration_dir.exists():
        print("ℹ️ Папка migration/ не найдена, миграции пропущены")
        return

    sqlite_migrations = sorted(
        [f for f in migration_dir.glob("*_sqlite.sql")],
        key=lambda p: p.name
    )

    if not sqlite_migrations:
        print("ℹ️ Нет SQL-миграций для SQLite")
        return

    with engine.connect() as conn:
        for mig_file in sqlite_migrations:
            try:
                sql = mig_file.read_text(encoding="utf-8")
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Применена миграция: {mig_file.name}")
            except Exception as e:
                print(f"⚠️ Пропущена миграция {mig_file.name}: {e}")
                conn.rollback()