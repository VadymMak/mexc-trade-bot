# app/routers/execution.py
from __future__ import annotations

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Body, HTTPException, Path, Query, Depends

from app.config.settings import settings
from app.execution.router import exec_router

# Subscribe symbols to market data before actions
from app.services.book_tracker import ensure_symbols_subscribed, get_all_quotes

# NEW: Import standardized idempotency system
from app.utils.idempotency import idempotent, get_idempotency_key

router = APIRouter(prefix="/api/exec", tags=["execution"])


# ---------------------------- helpers ----------------------------
def _parse_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


# ---------------------------- endpoints ----------------------------
@router.post("/place")
@idempotent(ttl_seconds=600)  # NEW: Standardized idempotency decorator
async def place_order(
    payload: dict = Body(
        ...,
        description=(
            "Example:\n"
            "{\n"
            '  "symbol": "BTCUSDT",\n'
            '  "side": "BUY",              // BUY | SELL\n'
            '  "qty":  0.01,               // base size\n'
            '  "price": 10000,             // optional hint price (paper) / limit price (live LIMIT)\n'
            '  "tag": "mm"                 // optional tag\n'
            "}\n"
        ),
    ),
    x_idempotency_key: Optional[str] = Depends(get_idempotency_key),  # NEW: Use Depends
) -> dict:
    sym = str(payload.get("symbol", "")).strip().upper()
    side = str(payload.get("side", "")).strip().upper()
    qty = _parse_float(payload.get("qty"), 0.0)
    price = _parse_float(payload.get("price"), 0.0)
    tag = str(payload.get("tag", "mm"))

    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be > 0")

    # make sure data stream is hot for this symbol (best-effort)
    try:
        await ensure_symbols_subscribed([sym])
    except Exception:
        pass

    port = exec_router.get_port()
    
    # SIMPLIFIED: Direct execution (decorator handles idempotency)
    coid = await port.place_maker(sym, side, price, qty, tag=tag)
    if not coid:
        raise HTTPException(status_code=400, detail="order rejected (risk guard or no price)")
    pos = await port.get_position(sym)
    return {"ok": True, "client_order_id": coid, "position": pos}


@router.post("/flatten/{symbol}")
@idempotent(ttl_seconds=600)  # NEW: Standardized idempotency decorator
async def flatten_symbol(
    symbol: str = Path(..., description="e.g. BTCUSDT"),
    x_idempotency_key: Optional[str] = Depends(get_idempotency_key),  # NEW: Use Depends
) -> dict:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    # keep quotes fresh (best-effort)
    try:
        await ensure_symbols_subscribed([sym])
    except Exception:
        pass

    port = exec_router.get_port()
    
    # SIMPLIFIED: Direct execution (decorator handles idempotency)
    await port.flatten_symbol(sym)
    pos = await port.get_position(sym)
    return {"ok": True, "flattened": sym, "position": pos}


@router.get("/position/{symbol}")
async def get_position(symbol: str = Path(..., description="e.g. BTCUSDT")) -> dict:
    """Read-only endpoint - no idempotency needed."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        await ensure_symbols_subscribed([sym])
    except Exception:
        pass
    port = exec_router.get_port()
    return await port.get_position(sym)


@router.get("/positions")
async def get_positions(symbols: Optional[List[str]] = Query(None)) -> list[dict]:
    """
    Read-only endpoint - no idempotency needed.
    If no symbols are provided, returns positions for all symbols we currently have quotes for.
    """
    port = exec_router.get_port()

    if symbols:
        # normalize + unique
        seen = set()
        syms: List[str] = []
        for s in symbols:
            if not s:
                continue
            ss = s.strip().upper()
            if ss and ss not in seen:
                seen.add(ss)
                syms.append(ss)
    else:
        quotes = await get_all_quotes()
        syms = [q["symbol"] for q in quotes]

    try:
        if syms:
            await ensure_symbols_subscribed(syms)
    except Exception:
        pass

    out: list[dict] = []
    for s in syms:
        out.append(await port.get_position(s))
    return out


@router.post("/cancel/{symbol}")
@idempotent(ttl_seconds=300)  # NEW: Shorter TTL for cancel operations
async def cancel_orders(
    symbol: str = Path(..., description="no-op in paper mode"),
    x_idempotency_key: Optional[str] = Depends(get_idempotency_key),  # NEW: Use Depends
) -> dict:
    """Cancel all orders for symbol. Idempotent to prevent double-cancellations."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    port = exec_router.get_port()
    await port.cancel_orders(sym)  # no-op in paper mode, exists for parity
    return {"ok": True, "canceled": sym}