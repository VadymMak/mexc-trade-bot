# app/db/session.py
from __future__ import annotations

from typing import  Generator
from sqlalchemy.orm import sessionmaker, Session

# The Engine is defined in app.db.engine
from app.db.engine import engine

# Create a session factory.
# Notes:
# - autocommit is removed in SQLAlchemy 2.x semantics (False by default, kept explicit for clarity)
# - autoflush=False gives you explicit control over when to flush
# - expire_on_commit=False keeps objects usable after commit (convenient for API layers)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,          # explicit; no autocommit in 2.x
    autoflush=False,
    expire_on_commit=False,
)

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a SQLAlchemy Session and ensures proper cleanup.

    Usage:
        from fastapi import Depends
        from sqlalchemy.orm import Session

        def endpoint(db: Session = Depends(get_db)):
            ...

    Behavior:
    - Yields a session for the request scope
    - Rolls back if an exception escapes the endpoint
    - Always closes the session
    """
    db: Session = SessionLocal()
    try:
        yield db
        # If your endpoints perform explicit commits, do nothing here.
        # If you want "commit-on-success" semantics, you could add db.commit() here,
        # but most apps prefer explicit commits in services/repositories.
    except Exception:
        # Defensive rollback so the connection isn't left in a bad transactional state
        try:
            db.rollback()
        except Exception:
            # If rollback itself fails, we still must close the session
            pass
        raise
    finally:
        db.close()


__all__ = ["SessionLocal", "get_db"]
