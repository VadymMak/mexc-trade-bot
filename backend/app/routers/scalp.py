# app/routers/scalp.py
"""
Scalp paper-trading router.

Stats and positions are served from in-memory cache populated
by the researcher service every 60s via internal push endpoints.

GET  /api/scalp/stats                  — aggregate session stats
GET  /api/scalp/positions              — latest N positions (closed + open)
GET  /api/scalp/export-dataset         — download all closed positions as CSV
POST /api/scalp/internal/stats-update  — called by researcher to push data
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/scalp", tags=["scalp"])
log = logging.getLogger(__name__)

# ── in-memory caches ─────────────────────────────────────────────────────────
# Populated by researcher via POST /internal/stats-update

_scalp_stats_cache: dict = {}
_scalp_positions_cache: list[dict] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public read endpoints ─────────────────────────────────────────────────────

@router.get("/stats")
async def get_scalp_stats() -> dict:
    """Aggregate scalp session stats."""
    if _scalp_stats_cache:
        return _scalp_stats_cache
    # Fallback empty stats while researcher hasn't pushed yet
    return {
        "open_count":    0,
        "closed_count":  0,
        "win_count":     0,
        "tp_rate":       0.0,
        "total_net_pnl": 0.0,
        "avg_net_pnl":   0.0,
        "avg_hold_sec":  0.0,
        "session": {
            "open_scalp":    0,
            "total_opened":  0,
            "total_closed":  0,
            "total_net_pnl": 0.0,
        },
        "last_updated": _now_iso(),
    }


@router.get("/positions")
async def get_scalp_positions(
    status: Optional[str] = None,
    limit:  int = 200,
) -> List[Dict[str, Any]]:
    """Recent scalp positions (newest first)."""
    rows = _scalp_positions_cache
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows[:limit]


# ── Internal push endpoint (called by researcher) ─────────────────────────────

@router.get("/export-dataset")
async def export_scalp_dataset() -> StreamingResponse:
    """
    Export all closed scalp_positions as CSV for analysis / ML.

    Columns: symbol, exchange, direction, hour_utc, day_of_week, trading_session,
             is_weekend, mm_repeat_score, buy_pressure, trade_velocity, book_imbalance,
             spread_cv, entry_price, exit_price, hold_seconds, exit_reason,
             deal_size_usdt, gross_pnl_usdt, net_pnl_usdt, pnl_pct, profitable
    """
    import csv
    import io
    import os
    import asyncpg
    from datetime import date

    neon_dsn = os.getenv("NEON_DATABASE_URL", "")
    if not neon_dsn:
        return StreamingResponse(
            iter(["error: NEON_DATABASE_URL not set"]),
            media_type="text/plain",
            status_code=500,
        )

    conn = await asyncpg.connect(dsn=neon_dsn, statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """
            SELECT symbol, exchange, direction,
                   entry_price, exit_price, opened_at, closed_at,
                   hold_seconds, exit_reason,
                   deal_size_usdt, gross_pnl_usdt, net_pnl_usdt,
                   mm_repeat_score, buy_pressure, trade_velocity,
                   book_imbalance, spread_cv
            FROM scalp_positions
            WHERE status = 'closed'
            ORDER BY opened_at ASC
            """
        )
    finally:
        await conn.close()

    def _session(h: int) -> str:
        if 0 <= h <= 6:   return "asia"
        if 7 <= h <= 12:  return "europe"
        if 13 <= h <= 15: return "overlap"
        if 16 <= h <= 21: return "us"
        return "quiet"

    buf    = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "symbol", "exchange", "direction",
        "hour_utc", "day_of_week", "trading_session", "is_weekend",
        "mm_repeat_score", "buy_pressure", "trade_velocity",
        "book_imbalance", "spread_cv",
        "entry_price", "exit_price",
        "hold_seconds", "exit_reason",
        "deal_size_usdt", "gross_pnl_usdt", "net_pnl_usdt",
        "pnl_pct", "profitable",
    ])

    for r in rows:
        net_pnl   = float(r["net_pnl_usdt"]  or 0)
        deal_size = float(r["deal_size_usdt"] or 10)
        opened_at = r["opened_at"]

        if opened_at:
            h       = opened_at.hour
            dow     = opened_at.weekday()
            session = _session(h)
            weekend = 1 if dow >= 5 else 0
        else:
            h = dow = session = weekend = ""

        def _f(val, digits: int = 4) -> str:
            return str(round(float(val), digits)) if val is not None else ""

        writer.writerow([
            r["symbol"], r["exchange"], r["direction"],
            h, dow, session, weekend,
            _f(r["mm_repeat_score"]),
            _f(r["buy_pressure"]),
            _f(r["trade_velocity"], 2),
            _f(r["book_imbalance"]),
            _f(r["spread_cv"]),
            _f(r["entry_price"], 6),
            _f(r["exit_price"],  6),
            r["hold_seconds"] or 0,
            r["exit_reason"] or "",
            deal_size,
            _f(r["gross_pnl_usdt"], 6),
            round(net_pnl, 6),
            round(net_pnl / deal_size * 100, 4),
            1 if net_pnl > 0 else 0,
        ])

    filename    = f"scalp_dataset_{date.today().isoformat()}.csv"
    csv_content = buf.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/internal/stats-update")
async def internal_scalp_stats_update(payload: dict) -> dict:
    """
    Called by researcher every 60s.
    Payload: { "stats": {...}, "session": {...}, "positions": [...] }
    """
    global _scalp_stats_cache, _scalp_positions_cache

    if "stats" in payload:
        _scalp_stats_cache = {**payload["stats"], "session": payload.get("session", {}), "last_updated": _now_iso()}
    if "positions" in payload:
        # Serialise datetime objects to ISO strings
        positions = []
        for p in payload["positions"]:
            row: dict = {}
            for k, v in p.items():
                row[k] = v.isoformat() if hasattr(v, "isoformat") else v
            positions.append(row)
        _scalp_positions_cache = positions

    log.debug("[Scalp] Stats cache updated: %d positions", len(_scalp_positions_cache))
    return {"ok": True}
