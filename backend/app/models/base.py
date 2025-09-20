# app/models/base.py
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

# ⚠️ Берём ЕДИНСТВЕННЫЙ engine из централизованного места
from app.db.engine import engine

# ───────────────── Naming convention (Alembic-friendly) ─────────────────
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base for all ORM models."""
    metadata = metadata


# Единый фабричный сессий, привязанный к импортированному engine
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def init_db() -> None:
    """
    Создать таблицы, если их ещё нет.
    В проде используйте Alembic миграции.
    """
    # Важно: импортируем модели, чтобы они зарегистрировались в Base.metadata
    from app.models import orders, positions, fills, sessions  # noqa: F401
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """
    Удобный контекст-менеджер для коротких операций с БД.
        with session_scope() as db:
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Генератор для FastAPI Depends(...)
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
