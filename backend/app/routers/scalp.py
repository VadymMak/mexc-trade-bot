# app/routers/scalp.py
"""
Scalp paper-trading router.

Stats and positions are served from in-memory cache populated
by the researcher service every 60s via internal push endpoints.

GET  /api/scalp/stats       — aggregate session stats
GET  /api/scalp/positions   — latest N positions (closed + open)
POST /api/scalp/internal/stats-update  — called by researcher to push data
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

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
