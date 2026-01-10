# app/routers/market.py
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, List, Optional, Dict

from fastapi import APIRouter, Request, HTTPException, Query, Path 
from fastapi.responses import StreamingResponse, Response as FastAPIResponse

from app.config.settings import settings
from app.services.book_tracker import (
    get_all_quotes,
    stream_quote_batches,
    ensure_symbols_subscribed,
)

router = APIRouter(prefix="/api/market", tags=["market"])

ALLOWED_ORIGINS: List[str] = list(getattr(settings, "cors_origins", []) or []) or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _origin_ok(origin: Optional[str]) -> bool:
    if not origin:
        return False
    if "*" in ALLOWED_ORIGINS:
        return True
    return origin in ALLOWED_ORIGINS


def _sse_format(
    event: str | None,
    data: dict | str,
    *,
    retry_ms: int | None = None,
    event_id: str | None = None,
) -> bytes:
    """
    Build a compliant SSE frame.
    - If retry_ms is provided, add a `retry:` hint for client reconnection backoff.
    - If event_id is provided, add an `id:` line for resumability.
    """
    if isinstance(data, (dict, list)):
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        payload = str(data)
    lines: list[str] = []
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    if event:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")  # end-of-event
    return ("\n".join(lines) + "\n").encode("utf-8")


@router.options("/stream")
async def _preflight_stream(request: Request) -> FastAPIResponse:
    origin = request.headers.get("origin", "")
    acr_headers = request.headers.get("access-control-request-headers", "")
    headers: Dict[str, str] = {}
    if _origin_ok(origin):
        headers["Access-Control-Allow-Origin"] = origin if "*" not in ALLOWED_ORIGINS else "*"
        # If wildcard is used, credentials must be false at the *CORS* layer; here we still echo for OPTIONS.
        headers["Access-Control-Allow-Credentials"] = "true" if origin != "*" else "false"
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
        headers["Access-Control-Allow-Headers"] = (
            acr_headers
            or "Content-Type, Authorization, If-Match, X-Idempotency-Key, Accept, Cache-Control, Last-Event-ID"
        )
        headers["Access-Control-Max-Age"] = "86400"
    return FastAPIResponse(status_code=204, headers=headers)


@router.get("/stream")
async def stream_market(
    request: Request,
    symbols: str = Query(..., description="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT"),
    interval_ms: int = Query(500, ge=100, le=60000, description="Push interval in ms"),
    emit_depth: bool = Query(True, description="Also emit 'depth' events with L2 data"),
) -> StreamingResponse:
    origin = request.headers.get("origin", "")

    if not symbols or not symbols.strip():
        raise HTTPException(status_code=400, detail="symbols query param is required")

    syms_raw = [s.strip().upper() for s in symbols.split(",") if s and s.strip()]
    # uniquify while preserving order
    seen: set[str] = set()
    syms: List[str] = []
    for s in syms_raw:
        if s not in seen:
            seen.add(s)
            syms.append(s)

    if not syms:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    # guard: enforce server-side bulk limit
    max_bulk = int(getattr(settings, "max_watchlist_bulk", 50) or 50)
    if len(syms) > max_bulk:
        raise HTTPException(status_code=400, detail=f"Too many symbols; max {max_bulk}")

    def _only_nonzero(quotes: List[dict]) -> List[dict]:
        # filter out placeholders (bid==ask==0)
        return [q for q in quotes if (q.get("bid", 0.0) > 0.0 or q.get("ask", 0.0) > 0.0)]

    def _l2_payload(quotes: List[dict]) -> List[dict]:
        # slimmer payload just for order book widgets
        out: List[dict] = []
        for q in quotes:
            bids = q.get("bids") or []
            asks = q.get("asks") or []
            if bids or asks:
                out.append({
                    "symbol": q.get("symbol", ""),
                    "bids": bids,
                    "asks": asks,
                    "ts_ms": q.get("ts_ms", 0),
                })
        return out

    # Basic event id counter per-connection (supports Last-Event-ID echo)
    last_event_id_header = request.headers.get("last-event-id")
    try:
        start_eid = int(last_event_id_header) if last_event_id_header is not None else 0
    except Exception:
        start_eid = 0
    eid = start_eid

    async def event_generator() -> AsyncGenerator[bytes, None]:
        # first message includes server-suggested retry backoff
        retry_ms = int(getattr(settings, "sse_retry_base_ms", 1000) or 1000)
        nonlocal eid
        eid += 1
        yield _sse_format("hello", {"type": "hello"}, retry_ms=retry_ms, event_id=str(eid))

        try:
            # 0) ensure live ingestion
            try:
                await ensure_symbols_subscribed(syms)
            except Exception:
                pass  # best-effort

            # 1) warm-up snapshot (brief wait for first nonzero ticks)
            warmup_ms = max(300, min(5000, int(interval_ms * 4)))
            deadline = asyncio.get_event_loop().time() + (warmup_ms / 1000.0)
            snapshot_quotes: List[dict] = []
            while True:
                try:
                    snapshot_quotes = _only_nonzero(await get_all_quotes(syms))
                except Exception:
                    snapshot_quotes = []
                if snapshot_quotes or asyncio.get_event_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.1)

            eid += 1
            # snapshot: first 'snapshot' (L1+derived), then optional 'depth'
            yield _sse_format("snapshot", {"type": "snapshot", "quotes": snapshot_quotes}, event_id=str(eid))
            if emit_depth:
                snap_depth = _l2_payload(snapshot_quotes)
                if snap_depth:
                    eid += 1
                    yield _sse_format("depth", {"type": "depth", "depth": snap_depth}, event_id=str(eid))

            # 2) streaming
            quote_stream = stream_quote_batches(syms, interval_ms=interval_ms)
            ping_every_ms = int(getattr(settings, "sse_ping_interval_ms", 15000) or 15000)
            next_ping = asyncio.get_event_loop().time() + (ping_every_ms / 1000.0)

            async for batch in quote_stream:
                if await request.is_disconnected():
                    break

                now = asyncio.get_event_loop().time()

                if not batch:
                    # send periodic ping even when batch exists to keep proxies happy
                    if now >= next_ping:
                        eid += 1
                        yield _sse_format("ping", {"type": "ping"}, event_id=str(eid))
                        next_ping = now + (ping_every_ms / 1000.0)
                    continue

                # always send quotes
                eid += 1
                yield _sse_format("quotes", {"type": "quotes", "quotes": _only_nonzero(batch)}, event_id=str(eid))

                # and (optionally) a separate 'depth' event if any item has L2
                if emit_depth:
                    l2 = _l2_payload(batch)
                    if l2:
                        eid += 1
                        yield _sse_format("depth", {"type": "depth", "depth": l2}, event_id=str(eid))

                # also keep ping cadence if interval_ms is very long
                if now >= next_ping:
                    eid += 1
                    yield _sse_format("ping", {"type": "ping"}, event_id=str(eid))
                    next_ping = now + (ping_every_ms / 1000.0)

        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception:
            # swallow to keep connection from crashing with stack traces
            return

    headers = {
        "Cache-Control": "no-cache",
        "Content-Type": "text/event-stream; charset=utf-8",
        "Connection": "keep-alive",
        "Vary": "Origin",
    }
    if _origin_ok(origin):
        headers["Access-Control-Allow-Origin"] = origin if "*" not in ALLOWED_ORIGINS else "*"
        # If wildcard origin is used, browsers disallow credentials anyway
        headers["Access-Control-Allow-Credentials"] = "true" if origin != "*" else "false"

    return StreamingResponse(event_generator(), headers=headers, media_type="text/event-stream")

@router.get("/{symbol}/quote")
async def get_quote(
    symbol: str = Path(..., description="Trading symbol, e.g. BTCUSDT")
) -> dict:
    """Get real-time quote for a single symbol."""
    import time
    from app.services import book_tracker
    
    symbol_upper = symbol.strip().upper()
    
    if not symbol_upper:
        raise HTTPException(status_code=400, detail="Symbol is required")
    
    # ✅ Get quote directly using the module function
    quote = await book_tracker.get_quote(symbol_upper)
    
    if not quote:
        raise HTTPException(
            status_code=404,
            detail=f"No quote data available for {symbol_upper}"
        )
    
    bid = float(quote.get('bid', 0))
    ask = float(quote.get('ask', 0))
    last = float(quote.get('last', 0))
    
    if bid <= 0 and ask <= 0:
        raise HTTPException(
            status_code=503,
            detail=f"No valid price data for {symbol_upper} (waiting for ticks)"
        )
    
    return {
        "symbol": symbol_upper,
        "bid": bid,
        "ask": ask,
        "last": last,
        "timestamp": int(time.time() * 1000)
    }
