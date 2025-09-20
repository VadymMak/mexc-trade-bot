from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional, Dict, Any, Awaitable, Callable

from fastapi import APIRouter, Body, HTTPException, Query, Header

from app.config.settings import settings
from app.execution.router import exec_router
from app.strategy.engine import StrategyEngine, StrategyParams
from app.services import book_tracker as bt_service
from app.infra.metrics import (
    strategy_entries_total,
    strategy_exits_total,
    strategy_open_positions,
    strategy_realized_pnl_total,
)

# ───────────────────────────── Optional idempotency service ─────────────────────────────
try:
    from app.services.strategy_service import StrategyService
    _strategy_service: StrategyService | None = StrategyService(
        ttl_seconds=settings.idempotency_window_sec
    )
except Exception:
    _strategy_service = None

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

# Bind engine to the workspace-aware execution port
_engine = StrategyEngine(exec_router.get_port(settings.workspace_id))


async def _idempotent_execute(
    op_name: str,
    idempotency_key: Optional[str],
    payload_fingerprint: Dict[str, Any],
    action: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    if _strategy_service and idempotency_key:
        return await _strategy_service.execute_idempotent(
            op_name=op_name,
            idempotency_key=idempotency_key,
            payload=payload_fingerprint,
            action=action,
        )
    result = await action()
    result.setdefault("idempotent", False)
    return result


# ───────────────────────────── Params ─────────────────────────────
@router.get("/params")
async def get_params() -> dict:
    p: StrategyParams = _engine.params()
    return asdict(p)


@router.put("/params")
async def set_params(patch: dict = Body(..., description="Partial update for StrategyParams")) -> dict:
    updated = _engine.update_params(patch or {})
    return {"ok": True, "params": updated}


# ───────────────────────────── Control ─────────────────────────────
@router.post("/start")
async def start_symbols(
    payload: dict = Body(...),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise HTTPException(status_code=400, detail="Field 'symbols' must be a non-empty list")

    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    async def _do() -> Dict[str, Any]:
        await _engine.start_symbols(syms)
        return {"ok": True, "started": syms}

    return await _idempotent_execute(
        op_name="strategy.start_symbols",
        idempotency_key=x_idempotency_key,
        payload_fingerprint={"symbols": syms},
        action=_do,
    )


@router.post("/stop")
async def stop_symbols(
    symbols: List[str] = Body(..., embed=True, description="Symbols to stop"),
    flatten: bool = Body(False, embed=True, description="Close position if true"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="Field 'symbols' must be a non-empty list")

    async def _do() -> Dict[str, Any]:
        await _engine.stop_symbols(syms, flatten=bool(flatten))
        return {"ok": True, "stopped": syms, "flatten": bool(flatten)}

    return await _idempotent_execute(
        op_name="strategy.stop_symbols",
        idempotency_key=x_idempotency_key,
        payload_fingerprint={"symbols": syms, "flatten": bool(flatten)},
        action=_do,
    )


@router.post("/stop-all")
async def stop_all(
    flatten: bool = Body(False, embed=True),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    async def _do() -> Dict[str, Any]:
        await _engine.stop_all(flatten=bool(flatten))
        return {"ok": True, "stopped_all": True, "flatten": bool(flatten)}

    return await _idempotent_execute(
        op_name="strategy.stop_all",
        idempotency_key=x_idempotency_key,
        payload_fingerprint={"flatten": bool(flatten)},
        action=_do,
    )


# ───────────────────────────── Positions ─────────────────────────────
@router.get("/position")
async def get_position(symbol: str = Query(..., description="e.g. HBARUSDT")) -> dict:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    port = exec_router.get_port(settings.workspace_id)
    return await port.get_position(sym)


@router.get("/positions")
async def get_positions(symbols: Optional[List[str]] = Query(None)) -> list[dict]:
    """
    Returns positions for provided symbols, or for all known symbols from the quote tracker.
    """
    port = exec_router.get_port(settings.workspace_id)
    if symbols:
        syms = [s.strip().upper() for s in symbols if s and s.strip()]
    else:
        quotes = await bt_service.get_all_quotes()  # use public API
        syms = [q["symbol"] for q in quotes]

    out: list[dict] = []
    for s in syms:
        out.append(await port.get_position(s))
    return out


# ───────────────────────────── Strategy metrics (JSON) ─────────────────────────────
def _collect_metric_samples(metric) -> list[Dict[str, Any]]:
    samples: list[Dict[str, Any]] = []
    try:
        base = getattr(metric, "_name", "")
        allowed_names = {base, f"{base}_total"}
        for m in metric.collect():
            for s in m.samples:
                if s.name.endswith("_created"):
                    continue
                if s.name not in allowed_names:
                    continue
                samples.append({"labels": dict(s.labels), "value": float(s.value)})
    except Exception:
        pass
    return samples


@router.get("/metrics")
async def strategy_metrics() -> dict:
    entries: Dict[str, float] = {}
    for s in _collect_metric_samples(strategy_entries_total):
        sym = s["labels"].get("symbol")
        if sym:
            entries[sym] = s["value"]

    exits: Dict[str, Dict[str, float]] = {}
    for s in _collect_metric_samples(strategy_exits_total):
        sym = s["labels"].get("symbol")
        reason = s["labels"].get("reason", "UNKNOWN")
        if sym:
            exits.setdefault(sym, {})[reason] = s["value"]

    open_flags: Dict[str, float] = {}
    for s in _collect_metric_samples(strategy_open_positions):
        sym = s["labels"].get("symbol")
        if sym:
            open_flags[sym] = s["value"]

    realized_pnl: Dict[str, float] = {}
    for s in _collect_metric_samples(strategy_realized_pnl_total):
        sym = s["labels"].get("symbol")
        if sym:
            realized_pnl[sym] = s["value"]

    return {
        "entries": entries,
        "exits": exits,
        "open_positions": open_flags,
        "realized_pnl": realized_pnl,
    }
