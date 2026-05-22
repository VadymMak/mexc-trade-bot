from fastapi import APIRouter, HTTPException, Header
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
    Reset ml_trade_outcomes for clean dataset collection.
    Adds ml_score + ml_would_block columns (idempotent), then deletes all rows.
    Works for both NeonDB (ML_DATABASE_URL set) and SQLite fallback.
    """
    _check_secret(x_admin_secret)

    from app.db.ml_engine import MLSessionLocal, ML_DB_ENABLED

    db = MLSessionLocal()
    try:
        before = db.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()[0]

        # Add new columns if missing — PostgreSQL supports IF NOT EXISTS, SQLite does not
        for col, col_type in [("ml_score", "FLOAT"), ("ml_would_block", "BOOLEAN")]:
            try:
                if ML_DB_ENABLED:
                    db.execute(text(f"ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS {col} {col_type} DEFAULT NULL"))
                else:
                    db.execute(text(f"ALTER TABLE ml_trade_outcomes ADD COLUMN {col} {col_type} DEFAULT NULL"))
            except Exception:
                pass  # column already exists

        db.execute(text("DELETE FROM ml_trade_outcomes"))
        db.commit()

        after = db.execute(text("SELECT COUNT(*) FROM ml_trade_outcomes")).fetchone()[0]
    finally:
        db.close()

    return {
        "status": "ok",
        "deleted": before,
        "remaining": after,
        "backend": "NeonDB" if ML_DB_ENABLED else "SQLite",
        "columns_added": ["ml_score", "ml_would_block"],
        "message": "Clean dataset collection started",
    }
