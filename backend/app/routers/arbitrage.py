# app/routers/arbitrage.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])
log = logging.getLogger(__name__)

# ─────────────────── in-memory caches ───────────────────
# Populated by researcher service via internal POST endpoints.
# Key: (symbol, exchange_long, exchange_short)  Value: spread dict
_spread_cache: dict[tuple, dict] = {}

# Stats pushed by researcher every 60s
# Shape: { "session": {...}, "pairs": [...] }
_stats_cache: dict = {}

# Queue items suggested by pair_promoter
# List of dicts with id, symbol, exchange_long, exchange_short, score, etc.
_queue_items: list[dict] = []
_queue_id_counter = 0


# ─────────────────── helpers ───────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sse(data: dict) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def _cache_or_mock() -> List[Dict[str, Any]]:
    """Return live data from cache if available, else fall back to mock."""
    if _spread_cache:
        return list(_spread_cache.values())
    return _mock_pairs()


# ─────────────────── mock data (fallback) ───────────────────

def _mock_pairs() -> List[Dict[str, Any]]:
    now = _now_iso()
    return [
        {"symbol": "BTCUSDT", "exchange_long": "gate",  "exchange_short": "mexc", "spread_pct": 0.042, "zscore":  1.8, "status": "signal",   "last_updated": now},
        {"symbol": "ETHUSDT", "exchange_long": "mexc",  "exchange_short": "gate", "spread_pct": 0.031, "zscore":  1.2, "status": "watching",  "last_updated": now},
        {"symbol": "SOLUSDT", "exchange_long": "gate",  "exchange_short": "mexc", "spread_pct": 0.018, "zscore":  0.6, "status": "watching",  "last_updated": now},
        {"symbol": "BTCUSDT", "exchange_long": "mexc",  "exchange_short": "gate", "spread_pct": 0.055, "zscore":  2.3, "status": "trading",   "last_updated": now},
        {"symbol": "ETHUSDT", "exchange_long": "gate",  "exchange_short": "mexc", "spread_pct": 0.009, "zscore": -0.3, "status": "watching",  "last_updated": now},
        {"symbol": "SOLUSDT", "exchange_long": "mexc",  "exchange_short": "gate", "spread_pct": 0.027, "zscore":  1.0, "status": "watching",  "last_updated": now},
    ]


def _mock_queue() -> List[Dict[str, Any]]:
    now = _now_iso()
    return [
        {
            "id": 1,
            "symbol": "BTCUSDT",
            "exchange_long": "gate",
            "exchange_short": "mexc",
            "score": 82.4,
            "win_rate": 0.67,
            "signals_per_day": 4.2,
            "avg_hold_minutes": 18.5,
            "avg_net_pnl_usdt": 0.31,
            "total_paper_trades": 127,
            "days_observed": 30,
            "submitted_at": now,
        },
        {
            "id": 2,
            "symbol": "ETHUSDT",
            "exchange_long": "mexc",
            "exchange_short": "gate",
            "score": 74.1,
            "win_rate": 0.61,
            "signals_per_day": 3.1,
            "avg_hold_minutes": 22.0,
            "avg_net_pnl_usdt": 0.24,
            "total_paper_trades": 89,
            "days_observed": 21,
            "submitted_at": now,
        },
    ]


# ─────────────────── REST endpoints ───────────────────

# ─────────────────── internal push endpoint (researcher → bot) ───────────────────

@router.post("/internal/spread-update")
async def internal_spread_update(spreads: List[Dict[str, Any]]) -> dict:
    """
    Called by the researcher service every ~5s with the latest spread snapshot.
    Updates in-memory cache — no auth required (internal Railway network only).
    """
    for item in spreads:
        key = (
            item.get("symbol", ""),
            item.get("exchange_long", ""),
            item.get("exchange_short", ""),
        )
        if all(key):
            _spread_cache[key] = item
    log.debug("[ARB] Spread cache updated: %d pairs", len(_spread_cache))
    return {"accepted": len(spreads)}


@router.post("/internal/stats-update")
async def internal_stats_update(body: Dict[str, Any]) -> dict:
    """
    Called by the researcher service every ~60s with paper-trading statistics.
    Caches session summary + per-pair breakdown.
    """
    global _stats_cache
    _stats_cache = {**body, "updated_at": _now_iso()}
    log.debug("[ARB] Stats cache updated: %d pairs", len(body.get("pairs", [])))
    return {"ok": True}


# ─────────────────── REST endpoints ───────────────────

@router.get("/research/pairs")
async def get_research_pairs() -> dict:
    """Monitored pairs with current spread data (live or mock fallback)."""
    pairs = _cache_or_mock()
    return {
        "pairs": pairs,
        "total": len(pairs),
        "updated_at": _now_iso(),
    }


@router.get("/research/stats")
async def get_research_stats() -> dict:
    """Paper-trading statistics from the researcher service."""
    if _stats_cache:
        return _stats_cache
    # Empty state before researcher pushes first batch
    return {
        "session": {
            "open_positions": 0,
            "total_opened": 0,
            "total_closed": 0,
            "total_net_pnl": 0.0,
            "breakeven_pct": 0.0,
        },
        "pairs": [],
        "updated_at": None,
    }


@router.post("/internal/queue-suggest")
async def internal_queue_suggest(body: Dict[str, Any]) -> dict:
    """
    Called by pair_promoter when a pair meets promotion criteria.
    Adds to the approval queue with a unique id.
    """
    global _queue_id_counter
    _queue_id_counter += 1
    item = {
        "id": _queue_id_counter,
        "symbol":         body.get("symbol", ""),
        "exchange_long":  body.get("exchange_long", ""),
        "exchange_short": body.get("exchange_short", ""),
        "score":          float(body.get("score", 0)),
        "win_rate":       float(body.get("win_rate", 0)),
        "signals_per_day": float(body.get("signals_per_day", 0)),
        "avg_hold_minutes": float(body.get("avg_hold_minutes", 0)),
        "avg_net_pnl_usdt": float(body.get("avg_net_pnl_usdt", 0)),
        "total_paper_trades": int(body.get("total_trades", 0)),
        "days_observed":  int(body.get("days_observed", 0)),
        "sharpe":         float(body.get("sharpe", 0)),
        "max_drawdown_pct": float(body.get("max_drawdown_pct", 0)),
        "submitted_at":   _now_iso(),
    }
    _queue_items.append(item)
    log.info("[ARB] Queue item added: %s %s/%s score=%.1f",
             item["symbol"], item["exchange_long"], item["exchange_short"], item["score"])
    return {"id": _queue_id_counter}


@router.get("/queue")
async def get_queue() -> dict:
    """Pairs pending manual approval from pair_promoter."""
    return {"items": _queue_items}


@router.post("/queue/{id}/approve")
async def approve_queue_item(id: int) -> dict:
    global _queue_items
    _queue_items = [i for i in _queue_items if i["id"] != id]
    log.info("[ARB] Approved queue item id=%s", id)
    return {"status": "approved", "id": id}


@router.post("/queue/{id}/reject")
async def reject_queue_item(id: int) -> dict:
    global _queue_items
    _queue_items = [i for i in _queue_items if i["id"] != id]
    log.info("[ARB] Rejected queue item id=%s", id)
    return {"status": "rejected", "id": id}


@router.post("/queue/{id}/snooze")
async def snooze_queue_item(id: int, body: dict = Body(default={})) -> dict:
    global _queue_items
    hours = int(body.get("hours", 24))
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    _queue_items = [i for i in _queue_items if i["id"] != id]
    log.info("[ARB] Snoozed queue item id=%s for %sh until %s", id, hours, until)
    return {"status": "snoozed", "until": until}


@router.get("/active")
async def get_active_positions() -> dict:
    """Active paper positions (empty until trading engine is wired)."""
    return {
        "positions": [],
        "total_paper_pnl": 0.0,
    }


# ─────────────────── SSE endpoint ───────────────────

@router.get("/sse")
async def arbitrage_sse() -> StreamingResponse:
    """
    Server-Sent Events stream for the Arbitrage dashboard.
    Sends spread_update every 10s and a heartbeat in between.
    Replaces polling on the frontend.
    """
    async def event_generator():
        try:
            # Immediate snapshot on connect
            yield _sse({"type": "spread_update", "pairs": _cache_or_mock()})

            while True:
                await asyncio.sleep(10)
                yield _sse({"type": "heartbeat", "ts": _now_iso()})
                yield _sse({"type": "spread_update", "pairs": _cache_or_mock()})

        except (asyncio.CancelledError, GeneratorExit):
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Content-Type": "text/event-stream; charset=utf-8",
            "Connection": "keep-alive",
        },
    )
