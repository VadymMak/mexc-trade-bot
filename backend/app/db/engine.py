# app/db/engine.py
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from app.config.settings import settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite://")


def _sqlite_path(url: str) -> Optional[Path]:
    """
    Extract filesystem path from a sqlite URL (sqlite:///relative.db or sqlite:////abs.db).
    Returns None for memory URLs (sqlite:// or sqlite:///:memory:).
    """
    if not _is_sqlite(url):
        return None
    # strip "sqlite://"
    tail = url[len("sqlite://") :]
    if not tail or tail == "/" or ":memory:" in tail:
        return None
    # urlparse expects a scheme; we've removed it. Treat remaining as path.
    # Normalize leading slashes (////abs -> /abs)
    p = Path(tail.lstrip("/"))
    # If original had four slashes (absolute), keep absolute
    if url.startswith("sqlite:////"):
        p = Path("/" + str(p))
    return p


# Resolve DB URL & echo from Settings (not raw env for consistency)
DATABASE_URL: str = settings.database_url.strip()
SQL_ECHO: bool = bool(getattr(settings, "sql_echo", False))

# Ensure SQLite directory exists (file-based only)
_sqlite_file = _sqlite_path(DATABASE_URL)
if _sqlite_file is not None:
    _sqlite_file.parent.mkdir(parents=True, exist_ok=True)

# SQLite needs check_same_thread disabled for FastAPI (threadpool workers)
connect_args = {"check_same_thread": False} if _is_sqlite(DATABASE_URL) else {}

# Create the Engine
engine: Engine = create_engine(
    DATABASE_URL,
    echo=SQL_ECHO,
    pool_pre_ping=True,  # validate pooled connections before using
    connect_args=connect_args,
    future=True,
)

# SQLite pragmas: enable WAL + foreign keys (+ stability tweaks)
if _is_sqlite(DATABASE_URL):
    @event.listens_for(engine, "connect")
    def _sqlite_on_connect(dbapi_conn, _):
        try:
            cur = dbapi_conn.cursor()
            # Avoid WAL for in-memory DBs
            if _sqlite_file is not None:
                # WAL improves concurrency for API servers
                cur.execute("PRAGMA journal_mode=WAL;")
                # Slightly safer than OFF for API; still performant
                cur.execute("PRAGMA synchronous=NORMAL;")
            # Stability/tuning
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.execute("PRAGMA busy_timeout=5000;")  # 5s wait on locks
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.close()
        except Exception:
            # Pragmas are best-effort; don't block startup
            pass


def _iter_sql_files(dirpath: Path, patterns: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for pat in patterns:
        files.extend(dirpath.glob(pat))
    return sorted(files, key=lambda p: p.name)


def apply_migrations(engine: Engine) -> None:
    """
    Apply SQL migrations from the `migration/` folder.

    Behavior:
    - SQLite: applies files matching `*_sqlite.sql` using executescript (multi-statement safe).
    - Other DBs: currently prints a note and returns (use Alembic in prod).
    - Idempotent: errors in individual files are logged and skipped to keep startup resilient.

    Call this after Base.metadata.create_all(...) at startup.
    """
    root = Path(__file__).resolve().parents[2]  # project root (…/backend)
    migration_dir = root / "migration"

    if not migration_dir.exists():
        print("ℹ️ migration/ folder not found — skipping migrations")
        return

    if _is_sqlite(DATABASE_URL):
        files = _iter_sql_files(migration_dir, ["*_sqlite.sql"])
        if not files:
            print("ℹ️ No SQLite migrations to apply")
            return

        # For SQLite, use raw DB-API executescript for multi-statement files.
        with engine.connect() as conn:
            raw = conn.connection  # DBAPI connection
            for f in files:
                try:
                    sql = f.read_text(encoding="utf-8")
                    # executescript ensures multiple statements run in sequence
                    raw.executescript(sql)  # type: ignore[attr-defined]
                    conn.commit()
                    print(f"✅ Применена миграция: {f.name}")
                except Exception as e:
                    print(f"⚠️ Пропущена миграция {f.name}: {e}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        return

    # Non-SQLite: leave to Alembic (prevent accidental apply of sqlite-specific SQL)
    print("ℹ️ Migrations are skipped for non-SQLite databases. Use Alembic in production.")
