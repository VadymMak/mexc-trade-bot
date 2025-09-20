# app/routers/execution.py
from __future__ import annotations

from typing import Optional, Dict, Any, Awaitable, Callable, List

from fastapi import APIRouter, Body, HTTPException, Path, Query, Header

from app.config.settings import settings
from app.execution.router import exec_router

# Optional idempotency (reuses your StrategyService infra)
try:
    from app.services.strategy_service import StrategyService
    _idem: StrategyService | None = StrategyService(ttl_seconds=settings.idempotency_window_sec)
except Exception:
    _idem = None

router = APIRouter(prefix="/api/exec", tags=["execution"])


# ---------------------------- helpers ----------------------------
async def _idempotent(
    op_name: str,
    key: Optional[str],
    payload: Dict[str, Any],
    action: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    if _idem and key:
        return await _idem.execute_idempotent(op_name, key, payload, action)
    res = await action()
    res.setdefault("idempotent", False)
    return res


# ---------------------------- endpoints ----------------------------
@router.post("/place")
async def place_order(
    payload: dict = Body(
        ...,
        description=(
            "Example:\n"
            "{\n"
            '  "symbol": "ATHUSDT",\n'
            '  "side": "BUY",              // BUY | SELL\n'
            '  "qty":  10.0,               // base size\n'
            '  "price": 0.1234,            // optional hint price\n'
            '  "tag": "mm"                 // optional tag\n'
            "}\n"
        ),
    ),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    sym = str(payload.get("symbol", "")).strip().upper()
    side = str(payload.get("side", "")).strip().upper()
    qty = float(payload.get("qty", 0) or 0)
    price = float(payload.get("price", 0) or 0)
    tag = str(payload.get("tag", "mm"))

    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be > 0")

    port = exec_router.get_port()  # keep consistent with strategy router usage

    async def _act() -> Dict[str, Any]:
        coid = await port.place_maker(sym, side, price, qty, tag=tag)
        if not coid:
            raise HTTPException(status_code=500, detail="failed to place order")
        pos = await port.get_position(sym)  # return position snapshot too
        return {"ok": True, "client_order_id": coid, "position": pos}

    return await _idempotent(
        op_name="exec.place",
        key=x_idempotency_key,
        payload={"symbol": sym, "side": side, "qty": qty, "price": price, "tag": tag},
        action=_act,
    )


@router.post("/flatten/{symbol}")
async def flatten_symbol(
    symbol: str = Path(..., description="e.g. HBARUSDT"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    port = exec_router.get_port()

    async def _act() -> Dict[str, Any]:
        await port.flatten_symbol(sym)
        pos = await port.get_position(sym)
        return {"ok": True, "flattened": sym, "position": pos}

    return await _idempotent(
        op_name="exec.flatten_symbol",
        key=x_idempotency_key,
        payload={"symbol": sym},
        action=_act,
    )


@router.get("/position/{symbol}")
async def get_position(symbol: str = Path(..., description="e.g. HBARUSDT")) -> dict:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    port = exec_router.get_port()
    return await port.get_position(sym)


@router.get("/positions")
async def get_positions(symbols: Optional[List[str]] = Query(None)) -> list[dict]:
    """
    If no symbols are provided, returns positions for all symbols we currently have quotes for.
    """
    from app.services.book_tracker import get_all_quotes
    port = exec_router.get_port()

    if symbols:
        syms = [s.strip().upper() for s in symbols if s and s.strip()]
    else:
        quotes = await get_all_quotes()
        syms = [q["symbol"] for q in quotes]

    out: list[dict] = []
    for s in syms:
        out.append(await port.get_position(s))
    return out


@router.post("/cancel/{symbol}")
async def cancel_orders(symbol: str = Path(..., description="no-op in paper mode")) -> dict:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    port = exec_router.get_port()
    await port.cancel_orders(sym)  # no-op in paper mode, exists for parity
    return {"ok": True, "canceled": sym}
