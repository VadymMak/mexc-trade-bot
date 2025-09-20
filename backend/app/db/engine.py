# app/db/engine.py
from __future__ import annotations

import os
from sqlalchemy import create_engine
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
