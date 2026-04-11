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
_symbol_states_cache: list[dict] = []
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


@router.post("/internal/symbol-states-update")
async def internal_symbol_states_update(body: List[Dict[str, Any]]) -> dict:
    """Called by researcher to sync symbol lifecycle states."""
    global _symbol_states_cache
    _symbol_states_cache = body
    return {"ok": True, "count": len(body)}


@router.get("/research/symbol-states")
async def get_symbol_states(state: str = "") -> dict:
    """Symbol lifecycle states (TESTING / APPROVED / BLACKLISTED)."""
    items = _symbol_states_cache
    if state:
        items = [s for s in items if s.get("state", "").upper() == state.upper()]
    return {"symbols": items, "total": len(items)}


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


_PHANTOM_EXCHANGES = {"binance", "bybit"}
_ZR_MIN_HOLD = 120


def _is_dirty(row: dict, exchange_long: str, exchange_short: str,
               exit_reason: str, hold_seconds: float) -> bool:
    """Return True if this trade is 'dirty' and should be excluded from clean export."""
    if exchange_long.lower() in _PHANTOM_EXCHANGES:
        return True
    if exchange_short.lower() in _PHANTOM_EXCHANGES:
        return True
    if exit_reason == "ZSCORE_REVERT" and hold_seconds < _ZR_MIN_HOLD:
        return True
    return False


@router.get("/research/export-dataset")
async def export_dataset(clean: bool = False) -> StreamingResponse:
    """
    Export all closed paper_positions as a CSV file for ML model training.
    Connects directly to Neon DB (NEON_DATABASE_URL env var).

    Features: entry_mode, entry_spread_pct, entry_zscore, spread_mean, spread_std,
              spread_zscore_ratio, spread_cv, hour_of_day, day_of_week
    Labels:   exit_reason, hold_seconds, net_pnl_usdt, pnl_pct, profitable
    """
    import csv
    import io
    import os
    import asyncpg

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
            SELECT
                symbol, exchange_long, exchange_short,
                entry_mode, entry_spread_pct, entry_zscore,
                spread_mean, spread_std,
                buy_pressure, trade_velocity, book_imbalance,
                exit_reason, exit_spread_pct, exit_zscore,
                deal_size_usdt, gross_pnl_usdt, net_pnl_usdt,
                hold_seconds, opened_at, closed_at
            FROM paper_positions
            WHERE status = 'closed'
              AND entry_spread_pct IS NOT NULL
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

    _FUNDING_SEC = (0, 28_800, 57_600)  # 00:00, 08:00, 16:00 UTC

    def _mins_to_funding(ts_ms: int) -> float:
        now_sec = (ts_ms // 1000) % 86_400
        return round(min(((f - now_sec) % 86_400) for f in _FUNDING_SEC) / 60, 2)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "symbol", "exchange_long", "exchange_short",
        "entry_mode", "entry_spread_pct", "entry_zscore",
        "spread_mean", "spread_std",
        "spread_zscore_ratio", "spread_cv",
        "buy_pressure", "trade_velocity", "book_imbalance",
        "hour_utc", "day_of_week",
        "trading_session", "is_weekend",
        "mins_to_funding",
        "deal_size_usdt",
        "exit_reason", "exit_spread_pct", "exit_zscore",
        "hold_seconds", "gross_pnl_usdt", "net_pnl_usdt",
        "pnl_pct", "profitable",
    ])

    for r in rows:
        ex_long   = r["exchange_long"] or ""
        ex_short  = r["exchange_short"] or ""
        ex_reason = r["exit_reason"] or ""
        hold_sec  = float(r["hold_seconds"] or 0)

        if clean and _is_dirty(r, ex_long, ex_short, ex_reason, hold_sec):
            continue

        e_spread  = float(r["entry_spread_pct"] or 0)
        s_mean    = float(r["spread_mean"] or 0)
        s_std     = float(r["spread_std"] or 0)
        net_pnl   = float(r["net_pnl_usdt"] or 0)
        deal_size = float(r["deal_size_usdt"] or 10)
        opened_at = r["opened_at"]

        ratio = round(e_spread / s_mean, 4) if s_mean > 0 else ""
        cv    = round(s_std / s_mean, 4)    if s_mean > 0 else ""

        if opened_at:
            h             = opened_at.hour
            dow           = opened_at.weekday()
            session       = _session(h)
            weekend       = 1 if dow >= 5 else 0
            ts_ms_entry   = int(opened_at.timestamp() * 1000)
            mins_to_fund  = _mins_to_funding(ts_ms_entry)
        else:
            h = dow = session = weekend = mins_to_fund = ""

        writer.writerow([
            r["symbol"], r["exchange_long"], r["exchange_short"],
            r["entry_mode"] or "large_spread", e_spread,
            round(float(r["entry_zscore"]), 4) if r["entry_zscore"] is not None else "",
            round(s_mean, 6) if s_mean else "",
            round(s_std, 6)  if s_std  else "",
            ratio, cv,
            round(float(r["buy_pressure"]),   4) if r["buy_pressure"]   is not None else "",
            round(float(r["trade_velocity"]), 2) if r["trade_velocity"] is not None else "",
            round(float(r["book_imbalance"]), 4) if r["book_imbalance"] is not None else "",
            h, dow, session, weekend,
            mins_to_fund,
            deal_size,
            r["exit_reason"] or "",
            float(r["exit_spread_pct"] or 0),
            round(float(r["exit_zscore"]), 4) if r["exit_zscore"] is not None else "",
            r["hold_seconds"] or 0,
            round(float(r["gross_pnl_usdt"] or 0), 6),
            round(net_pnl, 6),
            round(net_pnl / deal_size * 100, 4),
            1 if net_pnl > 0 else 0,
        ])

    from datetime import date
    suffix = "_clean" if clean else ""
    filename = f"arb_dataset_{date.today().isoformat()}{suffix}.csv"
    csv_content = buf.getvalue()

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
