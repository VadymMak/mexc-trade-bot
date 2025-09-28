# app/api/stream.py
from __future__ import annotations

import asyncio
import json
import contextlib
from typing import AsyncGenerator, List, Optional, Dict, Iterable, Any, Set

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, Response as FastAPIResponse

from app.config.settings import settings

router = APIRouter(prefix="/api/market", tags=["market"])

ALLOWED_ORIGINS: List[str] = list(getattr(settings, "cors_origins", []) or []) or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# ─────────────────────────────── Simple in-process broadcaster ───────────────────────────────
# Each connected client gets its own queue. External code can call `publish(...)` or `broadcast(...)`
# to fan out events (e.g., {"event":"pnl_tick","data":{...}}) to all connected clients.

_subscribers: Set[asyncio.Queue[Dict[str, Any]]] = set()
_SUBSCRIBER_QUEUE_SIZE = 1024  # prevent unbounded memory growth


def _coerce_msg(event_type_or_message: Any, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalize different publish signatures into a dict with 'event' and 'data'."""
    if isinstance(event_type_or_message, dict):
        msg = event_type_or_message
        event = str(msg.get("event") or msg.get("type") or "message")
        data = msg.get("data", None)
        if data is None:
            data = msg.get("payload", {})
        if not isinstance(data, (dict, list, str, int, float, bool)) and data is not None:
            data = {"value": str(data)}
        return {"event": event, "data": data}
    else:
        event = str(event_type_or_message or "message")
        data = payload if isinstance(payload, (dict, list)) else ({} if payload is None else {"value": payload})
        return {"event": event, "data": data}


def subscribe() -> asyncio.Queue[Dict[str, Any]]:
    q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[Dict[str, Any]]) -> None:
    _subscribers.discard(q)


def publish(event_type: Any, payload: Optional[Dict[str, Any]] = None) -> None:
    """
    Public API used by app.services.sse_publisher: publish("pnl_tick", {...})
    Also supports publish({"event":"...","data":{...}}).
    Non-blocking: drops if subscriber queue is full.
    """
    msg = _coerce_msg(event_type, payload)
    for q in tuple(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # drop instead of blocking – SSE is best-effort
            pass


def broadcast(message: Dict[str, Any]) -> None:
    """Alias for publish(dict)."""
    publish(message)

# ─────────────────────────────────────────────────────────────────────────────────────────────


def _origin_ok(origin: Optional[str]) -> bool:
    return bool(origin) and origin in ALLOWED_ORIGINS


def _sse_format(event: str | None, data: dict | list | str | int | float | bool) -> bytes:
    """Serialize an SSE event."""
    payload = (
        json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        if isinstance(data, (dict, list))
        else str(data)
    )
    lines = []
    if event:
        lines.append(f"event: {event}")
    for line in (payload.splitlines() or [""]):
        lines.append(f"data: {line}")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _clip_depth(levels: Iterable[Iterable[float]] | None, keep: int) -> list[tuple[float, float]]:
    """Sanitize, sort (best-first) and clip L2 levels to `keep`."""
    if not levels:
        return []
    out: list[tuple[float, float]] = []
    for row in levels:
        try:
            p = float(row[0]); q = float(row[1])
        except Exception:
            continue
        if p > 0 and q > 0:
            out.append((p, q))
    out.sort(key=lambda x: x[0], reverse=True)
    return out[: max(1, keep)]


def _pack_quote(q: dict, depth_limit: int) -> dict:
    """Pack a single quote dict with optional L2 + derived sizes."""
    sym = str(q.get("symbol") or "").upper()
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    mid = float(q.get("mid") or 0.0)
    spread_bps = float(q.get("spread_bps") or 0.0)
    ts_ms = int(q.get("ts_ms") or 0)

    bids = _clip_depth(q.get("bids"), depth_limit)
    asks_raw = _clip_depth(q.get("asks"), depth_limit)
    asks = sorted(asks_raw, key=lambda x: x[0])

    bid_qty = float(q.get("bidQty") or 0.0)
    ask_qty = float(q.get("askQty") or 0.0)
    if bid_qty <= 0 and bids:
        bid_qty = bids[0][1]
    if ask_qty <= 0 and asks:
        ask_qty = asks[0][1]

    out: dict = {
        "symbol": sym,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "ts_ms": ts_ms,
        "bidQty": bid_qty,
        "askQty": ask_qty,
    }
    if bids:
        out["bids"] = bids
    if asks:
        out["asks"] = asks
    return out


@router.options("/stream")
async def _preflight_stream(request: Request) -> FastAPIResponse:
    origin = request.headers.get("origin", "")
    acr_headers = request.headers.get("access-control-request-headers", "")
    headers: Dict[str, str] = {}
    if _origin_ok(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
        headers["Access-Control-Allow-Headers"] = (
            acr_headers
            or "Content-Type, Authorization, If-Match, X-Idempotency-Key, Accept, Cache-Control"
        )
        headers["Access-Control-Max-Age"] = "86400"
    return FastAPIResponse(status_code=204, headers=headers)


@router.get("/stream")
async def stream_market(
    request: Request,
    symbols: str = Query(..., description="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT"),
    interval_ms: int = Query(500, ge=100, le=60000, description="Push interval in ms"),
) -> StreamingResponse:
    # 🔁 Lazy-import heavy deps here, not at module import time
    with contextlib.suppress(Exception):
        from app.services.book_tracker import (
            get_all_quotes as _get_all_quotes,
            stream_quote_batches as _stream_quote_batches,
            ensure_symbols_subscribed as _ensure_symbols_subscribed,
        )
    # If import failed, define no-op fallbacks (SSE still works for custom events)
    def _noop(*args, **kwargs):
        return None

    _get_all_quotes = locals().get("_get_all_quotes") or _noop  # type: ignore
    _stream_quote_batches = locals().get("_stream_quote_batches") or (lambda *a, **k: _async_empty_gen())  # type: ignore
    _ensure_symbols_subscribed = locals().get("_ensure_symbols_subscribed") or (lambda *a, **k: None)  # type: ignore

    origin = request.headers.get("origin", "")
    if not symbols.strip():
        raise HTTPException(status_code=400, detail="symbols query param is required")

    syms: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    depth_limit = int(getattr(settings, "depth_limit", 10)) or 10

    # Each client subscribes to the broadcast bus
    sub_q = subscribe()

    async def event_generator() -> AsyncGenerator[bytes, None]:
        try:
            # Ensure provider stream/poller is running for these symbols
            with contextlib.suppress(Exception):
                res = _ensure_symbols_subscribed(syms)
                if asyncio.iscoroutine(res):
                    await res

            # Say hello so the FE can confirm connection
            yield _sse_format("hello", {"type": "hello"})

            # Warm-up snapshot
            warmup_ms = max(300, min(5000, int(interval_ms * 4)))
            deadline = asyncio.get_event_loop().time() + (warmup_ms / 1000.0)
            snap: list[dict] = []
            while True:
                with contextlib.suppress(Exception):
                    maybe = _get_all_quotes(syms)
                    if asyncio.iscoroutine(maybe):
                        arr = await maybe
                    else:
                        arr = maybe
                    if isinstance(arr, list):
                        snap = [
                            _pack_quote(q, depth_limit)
                            for q in arr
                            if float(q.get("bid", 0.0)) > 0.0 or float(q.get("ask", 0.0)) > 0.0
                        ]
                if snap or asyncio.get_event_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.1)

            yield _sse_format("snapshot", {"type": "snapshot", "quotes": snap})

            depth_snap = [
                {"symbol": q["symbol"], "bids": q.get("bids", []), "asks": q.get("asks", []), "ts_ms": q.get("ts_ms", 0)}
                for q in snap
                if q.get("bids") or q.get("asks")
            ]
            if depth_snap:
                yield _sse_format("depth", {"type": "depth", "depth": depth_snap})

            # Stream updates
            poll_sleep = max(0.05, min(1.0, interval_ms / 1000.0))
            agen = _stream_quote_batches(syms, interval_ms=interval_ms)

            while True:
                if await request.is_disconnected():
                    break

                # 1) Quotes batch (advance existing generator)
                packed: list[dict] = []
                try:
                    batch = await agen.__anext__() if hasattr(agen, "__anext__") else []
                except StopAsyncIteration:
                    agen = _stream_quote_batches(syms, interval_ms=interval_ms)
                    batch = []
                except Exception:
                    batch = []

                if batch:
                    packed = [
                        _pack_quote(q, depth_limit)
                        for q in (batch or [])
                        if (float(q.get("bid", 0.0)) > 0.0 or float(q.get("ask", 0.0)) > 0.0)
                    ]

                if packed:
                    yield _sse_format("quotes", {"type": "quotes", "quotes": packed})

                    depth_upd = [
                        {"symbol": q["symbol"], "bids": q.get("bids", []), "asks": q.get("asks", []), "ts_ms": q.get("ts_ms", 0)}
                        for q in packed
                        if q.get("bids") or q.get("asks")
                    ]
                    if depth_upd:
                        yield _sse_format("depth", {"type": "depth", "depth": depth_upd})
                else:
                    yield _sse_format("ping", {"type": "ping"})

                # 2) Drain broadcast queue (pnl_tick etc.)
                while True:
                    try:
                        msg = sub_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    event = str(msg.get("event") or "message")
                    data = msg.get("data", {})
                    yield _sse_format(event, data)

                await asyncio.sleep(poll_sleep)

        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception:
            return
        finally:
            unsubscribe(sub_q)

    headers = {
        "Cache-Control": "no-cache",
        "Content-Type": "text/event-stream; charset=utf-8",
        "Connection": "keep-alive",
        "Vary": "Origin",
    }
    if _origin_ok(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return StreamingResponse(event_generator(), headers=headers, media_type="text/event-stream")


# Utility: empty async generator
async def _async_empty_gen():
    if False:  # pragma: no cover
        yield None
