# app/api/stream.py
from __future__ import annotations

import asyncio
import json
import contextlib
from typing import AsyncGenerator, List, Optional, Dict, Iterable, Any, Set
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, Response as FastAPIResponse
import logging

from app.config.settings import settings

router = APIRouter(prefix="/api/market", tags=["market"])

ALLOWED_ORIGINS: List[str] = list(getattr(settings, "cors_origins", []) or []) or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# ─────────────────────────────── SSE Event Types ─────────────────────────────
class SSEEventType:
    """Standard SSE event type constants."""
    # Market data events (existing)
    HELLO = "hello"
    PING = "ping"
    SNAPSHOT = "snapshot"
    QUOTES = "quotes"
    DEPTH = "depth"
    
    # Scanner events (NEW)
    SCANNER_SNAPSHOT = "scanner_snapshot"
    SCANNER_UPDATE = "scanner_update"
    SCANNER_TIER_CHANGE = "scanner_tier_change"
    
    # System events (existing, via broadcast)
    PNL_TICK = "pnl_tick"
    POSITION_UPDATE = "position_update"
    ORDER_UPDATE = "order_update"

# ─────────────────────────────── Simple in-process broadcaster ───────────────────────────────
# Each connected client gets its own queue. External code can call `publish(...)` or `broadcast(...)`
# to fan out events (e.g., {"event":"pnl_tick","data":{...}}) to all connected clients.

_subscribers: Set[asyncio.Queue[Dict[str, Any]]] = set()
_SUBSCRIBER_QUEUE_SIZE = 1024  # prevent unbounded memory growth
_scanner_cache: Dict[str, tuple[dict, datetime]] = {}
_SCANNER_CACHE_TTL = timedelta(seconds=30)


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

async def _get_scanner_snapshot_cached(
    exchange: str = "gate",
    preset: str = "balanced",
    limit: int = 20,
    quote: str = "USDT",
) -> dict:
    """Cached wrapper around _get_scanner_snapshot."""
    import logging
    logger = logging.getLogger(__name__)
    
    # Create cache key
    cache_key = f"{exchange}:{preset}:{quote}:{limit}"
    
    # Check cache
    now = datetime.now()
    if cache_key in _scanner_cache:
        cached_data, cached_time = _scanner_cache[cache_key]
        age_seconds = (now - cached_time).total_seconds()
        if now - cached_time < _SCANNER_CACHE_TTL:
            logger.info(f"[SSE] Scanner cache HIT for {cache_key} (age: {age_seconds:.1f}s)")
            return cached_data
        else:
            logger.info(f"[SSE] Scanner cache EXPIRED for {cache_key} (age: {age_seconds:.1f}s)")
    
    # Cache miss - fetch fresh data
    logger.info(f"[SSE] Scanner cache MISS for {cache_key}, fetching...")
    data = await _get_scanner_snapshot(exchange, preset, limit, quote)
    
    # Store in cache (even if error, to avoid hammering API)
    _scanner_cache[cache_key] = (data, now)
    
    return data


async def _get_scanner_snapshot(
    exchange: str = "gate",
    preset: str = "balanced",
    limit: int = 20,
    quote: str = "USDT",
) -> dict:
    """
    Fetch current scanner results for SSE streaming.
    Returns formatted data ready to emit as scanner_snapshot event.
    """
    import logging
    import time  # ✅ NEW: Add time import
    logger = logging.getLogger(__name__)
    
    start_time = time.time()  # ✅ NEW: Track total time
    
    try:
        logger.info(f"[SSE] Scanner START: exchange={exchange}, preset={preset}, limit={limit}")
        
        # Lazy import to avoid circular dependencies
        from app.services.market_scanner import MarketScanner
        logger.info(f"[SSE] Import took {time.time() - start_time:.2f}s")  # ✅ NEW
        
        scanner = MarketScanner()
        logger.info(f"[SSE] Instance created at {time.time() - start_time:.2f}s")  # ✅ NEW
        
        # Add timeout to prevent hanging
        scan_start = time.time()  # ✅ NEW: Track scan time
        try:
            results = await asyncio.wait_for(
                scanner.scan_top(
                    exchange=exchange,
                    quote=quote,
                    preset=preset,
                    limit=limit,
                    fetch_candles=False,  # Disable candles for speed
                    rotation=False,
                    explain=False,  # Disable explain for speed
                ),
                timeout=15.0  # ✅ CHANGED: 15 second timeout (was 5.0)
            )
            scan_duration = time.time() - scan_start  # ✅ NEW
            logger.info(f"[SSE] Scanner DONE in {scan_duration:.2f}s, got {len(results)} results")  # ✅ NEW
            
        except asyncio.TimeoutError:
            total_elapsed = time.time() - start_time  # ✅ NEW
            logger.error(f"[SSE] Scanner TIMEOUT after 15s (total elapsed: {total_elapsed:.2f}s)")  # ✅ NEW
            # ✅ CHANGED: Return error dict instead of raising
            return {
                "type": "scanner_snapshot",
                "exchange": exchange,
                "preset": preset,
                "error": "Scanner timeout after 15 seconds",
                "timestamp": int(asyncio.get_event_loop().time() * 1000),
                "count": 0,
                "candidates": [],
            }
        
        # Format for SSE
        format_start = time.time()  # ✅ NEW
        formatted = {
            "type": "scanner_snapshot",
            "exchange": exchange,
            "preset": preset,
            "quote": quote,
            "timestamp": int(asyncio.get_event_loop().time() * 1000),
            "count": len(results),
            "candidates": [
                {
                    "symbol": r.get("symbol"),
                    "exchange": r.get("exchange"),
                    "bid": r.get("bid"),
                    "ask": r.get("ask"),
                    "spread_bps": r.get("spread_bps"),
                    "eff_spread_bps": r.get("eff_spread_bps"),
                    "usd_per_min": r.get("usd_per_min"),
                    "trades_per_min": r.get("trades_per_min"),
                    "depth5_bid_usd": r.get("depth5_bid_usd"),
                    "depth5_ask_usd": r.get("depth5_ask_usd"),
                    "score": r.get("score"),
                    "tier": r.get("tier", "B"),
                    "reason": r.get("reason"),
                }
                for r in results
            ],
        }
        format_duration = time.time() - format_start  # ✅ NEW
        total_duration = time.time() - start_time  # ✅ NEW
        logger.info(f"[SSE] Formatting took {format_duration:.2f}s, total {total_duration:.2f}s")  # ✅ NEW
        
        return formatted
        
    except Exception as e:
        total_elapsed = time.time() - start_time  # ✅ NEW
        logger.error(f"[SSE] Scanner ERROR at {total_elapsed:.2f}s: {type(e).__name__}: {str(e)}")  # ✅ NEW
        
        # ✅ NEW: Add traceback for debugging
        import traceback
        logger.error(traceback.format_exc())
        
        # Return error state instead of crashing
        return {
            "type": "scanner_snapshot",
            "exchange": exchange,
            "preset": preset,
            "error": f"{type(e).__name__}: {str(e)}",
            "timestamp": int(asyncio.get_event_loop().time() * 1000),
            "count": 0,
            "candidates": [],
        }


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

# Add this BEFORE the @router.get("/stream") endpoint in app/api/stream.py
# Around line 305

@router.get("/stream/debug")
async def debug_stream_params(
    request: Request,
    symbols: str = Query(..., description="Comma-separated symbols"),
    interval_ms: int = Query(500, ge=100, le=60000),
    include_scanner: bool = Query(False, description="Include scanner updates"),
    scanner_exchange: str = Query("gate"),
    scanner_preset: str = Query("balanced"),
    scanner_limit: int = Query(20, ge=1, le=100),
    scanner_interval_ms: int = Query(5000, ge=1000, le=60000),
):
    """Debug endpoint to verify parameter parsing."""
    return {
        "received_params": {
            "symbols": symbols,
            "interval_ms": interval_ms,
            "include_scanner": include_scanner,
            "scanner_exchange": scanner_exchange,
            "scanner_preset": scanner_preset,
            "scanner_limit": scanner_limit,
            "scanner_interval_ms": scanner_interval_ms,
        },
        "query_string": str(request.url.query),
        "url": str(request.url),
    }


@router.get("/stream")
async def stream_market(
    request: Request,
    symbols: str = Query(..., description="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT"),
    interval_ms: int = Query(500, ge=100, le=60000, description="Push interval in ms"),
    # NEW: Scanner-specific parameters
    include_scanner: bool = Query(False, description="Include scanner updates in stream"),
    scanner_exchange: str = Query("gate", description="Scanner exchange: gate|mexc|all"),
    scanner_preset: str = Query("balanced", description="Scanner preset: conservative|balanced|aggressive"),
    scanner_limit: int = Query(20, ge=1, le=100, description="Max scanner results"),
    scanner_interval_ms: int = Query(5000, ge=1000, le=60000, description="Scanner refresh interval"),
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

            # Stream updates - set scanner timer OUTSIDE the loop for proper scope
            # Replace lines 390-440 in app/api/stream.py with this:

            # Stream updates - set scanner timer OUTSIDE the loop for proper scope
            poll_sleep = max(0.05, min(1.0, interval_ms / 1000.0))
            agen = _stream_quote_batches(syms, interval_ms=interval_ms)

            # Initialize scanner timing
            logger = logging.getLogger(__name__)
            last_scanner_fetch = None
            
            # ✅ DEBUG: Log the include_scanner parameter
            logger.info(f"[SSE INIT] include_scanner={include_scanner}, scanner_interval_ms={scanner_interval_ms}")
            
            if include_scanner:
                # Force trigger immediately by setting to past time
                last_scanner_fetch = asyncio.get_event_loop().time() - (scanner_interval_ms / 1000.0)
                logger.info(f"[SSE INIT] Scanner ENABLED: last_scanner_fetch={last_scanner_fetch}, will trigger every {scanner_interval_ms}ms")
            else:
                logger.info(f"[SSE INIT] Scanner DISABLED")

            loop_iteration = 0  # ✅ DEBUG: Track iterations
            
            while True:
                loop_iteration += 1
                
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

                # 1.5) Scanner updates (if enabled)
                # ✅ DEBUG: Log every 10 iterations
                if loop_iteration % 10 == 0:
                    logger.info(f"[SSE Loop #{loop_iteration}] last_scanner_fetch={last_scanner_fetch}")
                
                if last_scanner_fetch is not None:
                    now = asyncio.get_event_loop().time()
                    elapsed = now - last_scanner_fetch
                    scanner_interval_sec = scanner_interval_ms / 1000.0

                    # ✅ DEBUG: Log every check (not just when triggered)
                    if loop_iteration % 5 == 0:  # Log every 5 iterations
                        logger.info(f"[SSE Loop #{loop_iteration}] Scanner timing: elapsed={elapsed:.2f}s, interval={scanner_interval_sec:.2f}s, will_trigger={elapsed >= scanner_interval_sec}")

                    if elapsed >= scanner_interval_sec:
                        logger.info(f"[SSE Loop #{loop_iteration}] Scanner TRIGGERED! elapsed={elapsed:.2f}s >= {scanner_interval_sec:.2f}s")
                        last_scanner_fetch = now

                        try:
                            logger.info(f"[SSE] Calling scanner: exchange={scanner_exchange}, preset={scanner_preset}, limit={scanner_limit}")
                            
                            # Use cached version
                            scanner_data = await _get_scanner_snapshot_cached(
                                exchange=scanner_exchange,
                                preset=scanner_preset,
                                limit=scanner_limit,
                                quote="USDT",
                            )
                            
                            logger.info(f"[SSE] Scanner returned {scanner_data.get('count', 0)} candidates")
                            
                            yield _sse_format(SSEEventType.SCANNER_SNAPSHOT, scanner_data)
                            
                            logger.info(f"[SSE] scanner_snapshot event EMITTED successfully")
                            
                        except Exception as scan_err:
                            logger.error(f"[SSE] Scanner ERROR: {scan_err}", exc_info=True)
                            yield _sse_format("error", {
                                "type": "error",
                                "source": "scanner",
                                "message": str(scan_err),
                            })
                else:
                    # ✅ DEBUG: Confirm scanner is disabled
                    if loop_iteration == 1:
                        logger.info(f"[SSE Loop] Scanner check skipped (last_scanner_fetch is None)")

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
