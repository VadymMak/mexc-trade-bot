from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, List, Optional, Dict

from fastapi import APIRouter, Request, HTTPException, Query
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
    return bool(origin) and origin in ALLOWED_ORIGINS


def _sse_format(event: str | None, data: dict | str) -> bytes:
    if isinstance(data, (dict, list)):
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        payload = str(data)
    lines = []
    if event:
        lines.append(f"event: {event}")
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
    origin = request.headers.get("origin", "")
    if not symbols.strip():
        raise HTTPException(status_code=400, detail="symbols query param is required")

    syms: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    async def event_generator() -> AsyncGenerator[bytes, None]:
        try:
            # 0) ensure live subscription (WS or REST fallback; may seed via REST)
            try:
                await ensure_symbols_subscribed(syms)
            except Exception:
                pass  # best-effort

            # 1) warm-up snapshot: wait briefly for first data to land
            warmup_ms = max(300, min(5000, int(interval_ms * 4)))
            deadline = asyncio.get_event_loop().time() + (warmup_ms / 1000.0)
            snapshot_quotes: List[dict] = []
            while True:
                try:
                    snapshot_quotes = await get_all_quotes(syms)
                except Exception:
                    snapshot_quotes = []
                # 🔎 filter out placeholders (bid==ask==0)
                snapshot_quotes = [
                    q for q in snapshot_quotes
                    if (float(q.get("bid", 0.0)) > 0.0 or float(q.get("ask", 0.0)) > 0.0)
                ]
                if snapshot_quotes or asyncio.get_event_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.1)

            yield _sse_format("snapshot", {"type": "snapshot", "quotes": snapshot_quotes})

            # 2) streaming updates
            quote_stream = stream_quote_batches(syms, interval_ms=interval_ms)
            async for batch in quote_stream:
                if await request.is_disconnected():
                    break
                # 🔎 filter placeholders in each batch too (defensive)
                batch = [
                    q for q in (batch or [])
                    if (float(q.get("bid", 0.0)) > 0.0 or float(q.get("ask", 0.0)) > 0.0)
                ]
                if batch:
                    yield _sse_format("quotes", {"type": "quotes", "quotes": batch})
                else:
                    yield _sse_format("ping", {"type": "ping"})
        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception:
            return

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
