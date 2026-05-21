from fastapi import APIRouter, HTTPException, Header
from app.db.engine import engine
from sqlalchemy import text
import os

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")


def _check_secret(x_admin_secret: str) -> None:
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/reset-ml-table")
def reset_ml_table(x_admin_secret: str = Header(default="")):
    """
    One-time reset of ml_trade_outcomes for clean dataset collection.
    Adds ml_score + ml_would_block columns, then deletes all rows.
    """
    _check_secret(x_admin_secret)

    with engine.connect() as conn:
        # Add new columns — SQLite does not support IF NOT EXISTS in ALTER TABLE
        for col, col_type in [("ml_score", "REAL"), ("ml_would_block", "INTEGER")]:
            try:
                conn.execute(text(f"ALTER TABLE ml_trade_outcomes ADD COLUMN {col} {col_type} DEFAULT NULL"))
                conn.commit()
            except Exception:
                pass  # column already exists

        before = conn.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()[0]

        conn.execute(text("DELETE FROM ml_trade_outcomes"))
        conn.commit()

        after = conn.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()[0]

    return {
        "status": "ok",
        "deleted": before,
        "remaining": after,
        "columns_added": ["ml_score", "ml_would_block"],
        "message": "Clean dataset collection started",
    }
