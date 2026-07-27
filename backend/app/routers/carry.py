"""
Carry (funding/basis) research router — ADDITIVE.

Mirrors the arbitrage research export: streams the funding_basis_snapshots
table as CSV so the carry data can be analyzed offline. Read-only; does not
touch arb tables or endpoints.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/carry", tags=["carry"])


@router.get("/export-dataset")
async def export_dataset() -> StreamingResponse:
    """
    Export funding_basis_snapshots as CSV for carry research.
    Reads from the same DB the arb export uses (local trading_bot via MLSessionLocal).
    """
    import csv
    import io
    from app.db.ml_engine import MLSessionLocal
    from sqlalchemy import text

    db = MLSessionLocal()
    try:
        rows = db.execute(text("""
            SELECT *
            FROM funding_basis_snapshots
            ORDER BY ts ASC
        """)).fetchall()
        keys = db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'funding_basis_snapshots'
            ORDER BY ordinal_position
        """)).fetchall()
        columns = [k[0] for k in keys]
    finally:
        db.close()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()

        for row in rows:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(list(row))
            yield buf.getvalue()

    filename = f"carry_{len(rows)}_snapshots.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
