# app/db/session.py
from __future__ import annotations

from typing import Iterator
from sqlalchemy.orm import sessionmaker, Session

# Ожидаем, что в engine.py определён объект SQLAlchemy engine
from app.db.engine import engine

# Фабрика сессий (без автокоммита и без автофлаша)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def get_db() -> Iterator[Session]:
    """
    FastAPI dependency: выдаёт SQLAlchemy Session и корректно закрывает её.
    Пример использования:
        def endpoint(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
