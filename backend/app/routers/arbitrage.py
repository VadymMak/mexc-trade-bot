# app/routers/arbitrage.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Query
from fastapi.responses import StreamingResponse, JSONResponse

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

# Exchanges used only as reference/research feeds — NOT tradable venues.
# Binance/Bybit: mark-price references, structurally diverge from Tier-3.
# mexc_spot: spot prices for basis analysis only, same asset as mexc futures.
_PHANTOM_EXCHANGES: frozenset[str] = frozenset({"binance", "bybit", "mexc_spot"})


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

    Binance and Bybit are mark-price reference feeds only — their prices
    structurally diverge from Tier-3 futures (Gate/MEXC/KuCoin), producing
    phantom spreads of 10-170% that never mean-revert.  Filter them out here
    so they never appear in the frontend display.
    """
    accepted = 0
    for item in spreads:
        ex_long  = item.get("exchange_long",  "").lower()
        ex_short = item.get("exchange_short", "").lower()
        # Drop pairs that involve reference-only exchanges
        if ex_long in _PHANTOM_EXCHANGES or ex_short in _PHANTOM_EXCHANGES:
            continue
        key = (
            item.get("symbol", ""),
            ex_long,
            ex_short,
        )
        if all(key):
            _spread_cache[key] = item
            accepted += 1
    log.debug("[ARB] Spread cache updated: %d pairs (filtered phantoms)", len(_spread_cache))
    return {"accepted": accepted}


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


# _PHANTOM_EXCHANGES defined at top of module (line ~36)
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
async def export_dataset() -> StreamingResponse:
    """
    Export ml_trade_outcomes as CSV for ML model training.
    Reads from NeonDB (ML_DATABASE_URL) — full 80+ feature dataset.
    Only closed trades (exit_time IS NOT NULL).
    """
    import csv
    import io
    from app.db.ml_engine import MLSessionLocal, ML_DB_ENABLED
    from sqlalchemy import text

    db = MLSessionLocal()
    try:
        rows = db.execute(text("""
            SELECT *
            FROM ml_trade_outcomes
            WHERE exit_time IS NOT NULL
            ORDER BY entry_time ASC
        """)).fetchall()
        keys = db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'ml_trade_outcomes'
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

    filename = f"ml_dataset_{len(rows)}_trades.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/active")
async def get_active_positions() -> dict:
    """Active paper positions — queried from NeonDB paper_positions table."""
    import os
    import asyncpg
    from datetime import datetime, timezone

    neon_dsn = os.getenv("NEON_DATABASE_URL", "")
    if not neon_dsn:
        log.warning("get_active_positions: NEON_DATABASE_URL not set")
        return {"positions": [], "total_paper_pnl": 0.0}

    try:
        conn = await asyncpg.connect(dsn=neon_dsn, statement_cache_size=0)
        try:
            rows = await conn.fetch(
                """
                SELECT id, symbol, exchange_long, exchange_short,
                       entry_spread_pct, deal_size_usdt, opened_at
                FROM paper_positions
                WHERE status = 'open'
                ORDER BY opened_at DESC
                LIMIT 100
                """
            )
        finally:
            await conn.close()
    except Exception as exc:
        log.warning("get_active_positions: DB error: %s", exc)
        return {"positions": [], "total_paper_pnl": 0.0}

    now = datetime.now(timezone.utc)
    positions = []
    for r in rows:
        symbol      = r["symbol"]
        ex_long     = r["exchange_long"]
        ex_short    = r["exchange_short"]
        entry_spread = float(r["entry_spread_pct"] or 0)
        size_usdt    = float(r["deal_size_usdt"]   or 10)
        opened_at    = r["opened_at"]
        hold_minutes = (now - opened_at).total_seconds() / 60 if opened_at else 0

        cache_key = (symbol, ex_long, ex_short)
        current_spread = float(_spread_cache.get(cache_key, {}).get("spread_pct", entry_spread))

        unrealized_pnl = round(
            (entry_spread - current_spread) / entry_spread * size_usdt * 0.5
            if entry_spread > 0 else 0.0,
            4,
        )
        positions.append({
            "id":                  r["id"],
            "symbol":              symbol,
            "exchange_long":       ex_long,
            "exchange_short":      ex_short,
            "entry_spread_pct":    entry_spread,
            "current_spread_pct":  current_spread,
            "size_usdt":           size_usdt,
            "opened_at":           opened_at.isoformat() if opened_at else "",
            "hold_minutes":        round(hold_minutes, 1),
            "unrealized_pnl_usdt": unrealized_pnl,
            "mode":                "paper",
        })

    total_pnl = round(sum(p["unrealized_pnl_usdt"] for p in positions), 4)
    return {"positions": positions, "total_paper_pnl": total_pnl}


# ─────────────────── Live analytics endpoint ───────────────────

@router.get("/analyze")
async def analyze_trades(hours: int = Query(24, ge=1, le=720)) -> dict:
    """
    Live analytics from NeonDB paper_positions.
    Returns overview, vel-tier breakdown, exit reasons, daily PnL,
    per-symbol stats, hourly pattern, and current open positions.
    """
    import os
    import asyncpg

    neon_dsn = os.getenv("NEON_DATABASE_URL", "")
    if not neon_dsn:
        return JSONResponse({"error": "NEON_DATABASE_URL not set"}, status_code=500)

    def _f(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except Exception:
            return 0.0

    conn = await asyncpg.connect(dsn=neon_dsn, statement_cache_size=0)
    try:
        # 1. Overview
        ov = await conn.fetchrow(
            """
            SELECT
                COUNT(*)                                              AS total_trades,
                COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT')  AS wins,
                COALESCE(SUM(net_pnl_usdt),   0)                     AS net_pnl,
                COALESCE(SUM(gross_pnl_usdt), 0)                     AS gross_pnl,
                COALESCE(SUM(fee_usdt),        0)                     AS total_fees,
                AVG(hold_seconds)                                     AS avg_hold_seconds,
                AVG(deal_size_usdt)                                   AS avg_size
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '1 hour' * $1
            """,
            hours,
        )
        total = int(ov["total_trades"] or 0)
        wins  = int(ov["wins"] or 0)
        net   = _f(ov["net_pnl"])
        overview = {
            "total_trades":      total,
            "wins":              wins,
            "win_rate":          round(wins / total, 4) if total else 0.0,
            "net_pnl":           round(net, 4),
            "gross_pnl":         round(_f(ov["gross_pnl"]), 4),
            "total_fees":        round(_f(ov["total_fees"]), 4),
            "avg_hold_seconds":  round(_f(ov["avg_hold_seconds"]), 1),
            "avg_size":          round(_f(ov["avg_size"]), 2),
            "avg_pnl_per_trade": round(net / total, 4) if total else 0.0,
        }

        # 2. Vel-tier breakdown
        tier_rows = await conn.fetch(
            """
            SELECT deal_size_usdt                                        AS tier,
                   COUNT(*)                                              AS trades,
                   COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT')  AS wins,
                   COALESCE(SUM(net_pnl_usdt), 0)                       AS net_pnl,
                   AVG(net_pnl_usdt)                                     AS avg_pnl
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '1 hour' * $1
            GROUP BY deal_size_usdt
            ORDER BY deal_size_usdt
            """,
            hours,
        )
        tiers = []
        for r in tier_rows:
            tr = int(r["trades"] or 0)
            tw = int(r["wins"] or 0)
            tiers.append({
                "tier":     _f(r["tier"]),
                "trades":   tr,
                "wins":     tw,
                "win_rate": round(tw / tr, 4) if tr else 0.0,
                "net_pnl":  round(_f(r["net_pnl"]), 4),
                "avg_pnl":  round(_f(r["avg_pnl"]), 4),
            })

        # 3. Exit reasons
        exit_rows = await conn.fetch(
            """
            SELECT exit_reason, COUNT(*) AS count
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '1 hour' * $1
            GROUP BY exit_reason
            ORDER BY count DESC
            """,
            hours,
        )
        exit_reasons = [
            {"reason": r["exit_reason"] or "UNKNOWN", "count": int(r["count"])}
            for r in exit_rows
        ]

        # 4. PnL by day — always last 30 days (independent of hours param)
        daily_rows = await conn.fetch(
            """
            SELECT DATE(closed_at AT TIME ZONE 'UTC')                   AS day,
                   COUNT(*)                                              AS trades,
                   COALESCE(SUM(net_pnl_usdt), 0)                       AS net_pnl,
                   COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT')  AS wins
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '30 days'
            GROUP BY DATE(closed_at AT TIME ZONE 'UTC')
            ORDER BY day
            """
        )
        daily_pnl = [
            {
                "day":     r["day"].isoformat() if r["day"] else "",
                "trades":  int(r["trades"] or 0),
                "net_pnl": round(_f(r["net_pnl"]), 4),
                "wins":    int(r["wins"] or 0),
            }
            for r in daily_rows
        ]

        # 5. Per-symbol stats
        sym_rows = await conn.fetch(
            """
            SELECT symbol,
                   COUNT(*)                                              AS trades,
                   COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT')  AS wins,
                   COALESCE(SUM(net_pnl_usdt), 0)                       AS net_pnl,
                   AVG(hold_seconds)                                     AS avg_hold,
                   AVG(deal_size_usdt)                                   AS avg_size
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '1 hour' * $1
            GROUP BY symbol
            ORDER BY net_pnl DESC
            """,
            hours,
        )
        symbols = []
        for r in sym_rows:
            tr = int(r["trades"] or 0)
            tw = int(r["wins"] or 0)
            symbols.append({
                "symbol":   r["symbol"],
                "trades":   tr,
                "wins":     tw,
                "win_rate": round(tw / tr, 4) if tr else 0.0,
                "net_pnl":  round(_f(r["net_pnl"]), 4),
                "avg_hold": round(_f(r["avg_hold"]), 1),
                "avg_size": round(_f(r["avg_size"]), 2),
            })

        # 6. Hourly pattern (UTC)
        hourly_rows = await conn.fetch(
            """
            SELECT EXTRACT(HOUR FROM closed_at AT TIME ZONE 'UTC')      AS hour_utc,
                   COUNT(*)                                              AS trades,
                   COUNT(*) FILTER (WHERE exit_reason = 'TAKE_PROFIT')  AS wins,
                   COALESCE(SUM(net_pnl_usdt), 0)                       AS net_pnl
            FROM paper_positions
            WHERE status = 'closed'
              AND closed_at > NOW() - INTERVAL '1 hour' * $1
            GROUP BY hour_utc
            ORDER BY hour_utc
            """,
            hours,
        )
        hourly = []
        for r in hourly_rows:
            tr = int(r["trades"] or 0)
            tw = int(r["wins"] or 0)
            hourly.append({
                "hour":     int(_f(r["hour_utc"])),
                "trades":   tr,
                "wins":     tw,
                "win_rate": round(tw / tr, 4) if tr else 0.0,
                "net_pnl":  round(_f(r["net_pnl"]), 4),
            })

        # 7. Open positions summary (only recent — stale positions are marked 'stale' on startup)
        open_row = await conn.fetchrow(
            """
            SELECT COUNT(*)                         AS open_count,
                   COALESCE(SUM(deal_size_usdt), 0) AS open_exposure
            FROM paper_positions
            WHERE status = 'open'
              AND opened_at > NOW() - INTERVAL '4 hours'
            """
        )
        open_data = {
            "count":    int(open_row["open_count"] or 0),
            "exposure": round(_f(open_row["open_exposure"]), 2),
        }
    finally:
        await conn.close()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hours":        hours,
        "overview":     overview,
        "tiers":        tiers,
        "exit_reasons": exit_reasons,
        "daily_pnl":    daily_pnl,
        "symbols":      symbols,
        "hourly":       hourly,
        "open":         open_data,
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
