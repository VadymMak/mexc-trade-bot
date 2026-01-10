from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional, Dict, Any, Awaitable, Callable, Union

from fastapi import APIRouter, Body, HTTPException, Query, Header

from app.config.settings import settings
from app.execution.router import exec_router
from app.strategy.engine import StrategyEngine, StrategyParams
from app.services import book_tracker as bt_service
from app.services.book_tracker import ensure_symbols_subscribed
from app.services.price_poller import get_poller

# Metrics are optional; keep imports guarded
try:
    from app.infra.metrics import (
        strategy_entries_total,
        strategy_exits_total,
        strategy_open_positions,
        strategy_realized_pnl_total,
        # extra metrics (may be unused here but kept for parity)
        strategy_symbols_running,
        strategy_trade_pnl_bps,
        strategy_trade_duration_seconds,
        strategy_edge_bps_at_entry,
    )
    _METRICS_OK = True
except Exception:
    _METRICS_OK = False

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


def _normalize_symbols_param(symbols: Union[str, List[str], None]) -> List[str]:
    """
    Accepts:
      - None → []
      - "BTCUSDT" → ["BTCUSDT"]
      - "BTCUSDT,ETHUSDT" → ["BTCUSDT","ETHUSDT"]
      - ["BTCUSDT","ETHUSDT"] → ["BTCUSDT","ETHUSDT"]
    """
    if symbols is None:
        return []
    if isinstance(symbols, list):
        raw = symbols
    else:
        raw = [symbols]
    out: List[str] = []
    for item in raw:
        if not item:
            continue
        parts = str(item).split(",")
        for p in parts:
            s = p.strip().upper()
            if s:
                out.append(s)
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _enforce_bulk_limit(syms: List[str]) -> None:
    max_bulk = int(getattr(settings, "max_watchlist_bulk", 50) or 50)
    if len(syms) > max_bulk:
        raise HTTPException(status_code=400, detail=f"Too many symbols; max {max_bulk}")


# ───────────────────────────── Params ─────────────────────────────
@router.get("/params")
async def get_params() -> dict:
    p: StrategyParams = _engine.params()
    return asdict(p)


@router.put("/params")
async def set_params(patch: dict = Body(..., description="Partial update for StrategyParams")) -> dict:
    """
    Update strategy parameters. 
    Returns 409 if there are open positions (must close positions first).
    """
    # Check if there are any open positions
    port = exec_router.get_port(settings.workspace_id)
    
    try:
        # Get all known symbols from quote tracker
        quotes = await bt_service.get_all_quotes()
        symbols_to_check = [q["symbol"] for q in quotes if q.get("symbol")]
        
        # Check positions for each symbol
        open_positions = []
        for sym in symbols_to_check:
            try:
                pos = await port.get_position(sym)
                qty = pos.get("qty", 0.0)
                if qty and abs(qty) > 0.0001:  # Has position
                    open_positions.append({
                        "symbol": sym,
                        "qty": qty,
                        "avg_price": pos.get("avg_price", 0.0)
                    })
            except Exception:
                continue
        
        # If there are open positions, reject the update
        if open_positions:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Cannot update parameters while positions are open",
                    "message": "Please close all positions before changing strategy parameters",
                    "open_positions": open_positions,
                    "count": len(open_positions)
                }
            )
        
        # No open positions - safe to update
        updated = _engine.update_params(patch or {})
        return {
            "ok": True, 
            "params": updated,
            "applied_immediately": True,
            "message": "Parameters updated successfully (no restart required)"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # If we can't check positions, be conservative and allow update
        # (better than blocking legitimate updates)
        updated = _engine.update_params(patch or {})
        return {
            "ok": True, 
            "params": updated,
            "applied_immediately": True,
            "warning": "Could not verify positions, parameters updated anyway"
        }


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
    _enforce_bulk_limit(syms)

    async def _do() -> Dict[str, Any]:
        # Start price poller as primary data source
        poller = get_poller()
        if not poller.running:
            # Collect all symbols we might need
            all_syms = syms.copy()
            try:
                # Add any already-running symbols
                quotes = await bt_service.get_all_quotes()
                for q in quotes:
                    sym = q.get("symbol")
                    if sym and sym not in all_syms:
                        all_syms.append(sym)
            except:
                pass
            await poller.start(all_syms)
        else:
            print(f"ℹ️ Poller already running, adding symbols: {syms}")
        
        # Try WebSocket as optional enhancement (don't block if fails)
        try:
            await ensure_symbols_subscribed(syms)
        except Exception as e:
            print(f"⚠️ WebSocket subscription failed (OK, using REST): {e}")
        
        await _engine.start_symbols(syms)
        return {"ok": True, "started": syms, "running": syms}

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
    _enforce_bulk_limit(syms)

    async def _do() -> Dict[str, Any]:
        # ensure we have fresh data if flatten requested
        if flatten:
            try:
                await ensure_symbols_subscribed(syms)
            except Exception:
                pass
        await _engine.stop_symbols(syms, flatten=bool(flatten))
        # фронт ожидает `flattened: string[]`
        flattened = syms if bool(flatten) else []
        return {"ok": True, "stopped": syms, "flattened": flattened}

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
        # при флаттене подстрахуемся подпиской на все известные символы (best-effort)
        if flatten:
            try:
                quotes = await bt_service.get_all_quotes()
                syms = [q["symbol"] for q in quotes if q.get("symbol")]
                if syms:
                    await ensure_symbols_subscribed(syms)
            except Exception:
                pass

        await _engine.stop_all(flatten=bool(flatten))
        # для совместимости с фронтом вернём ожидаемую форму
        return {"ok": True, "stopped": [], "flattened": []}

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
    try:
        return await port.get_position(sym)
    except Exception:
        # defensive: never 500 for a bad symbol
        return {"symbol": sym, "qty": 0.0, "avg_price": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0, "ts_ms": 0}


@router.get("/positions")
async def get_positions(
    symbols: Union[str, List[str], None] = Query(
        None,
        description="Symbol list; supports repeated keys (?symbols=A&symbols=B) or CSV (?symbols=A,B).",
    )
) -> list[dict]:
    """
    Returns positions for provided symbols, or for all known symbols from the quote tracker.
    """
    port = exec_router.get_port(settings.workspace_id)

    syms = _normalize_symbols_param(symbols)
    if syms:
        _enforce_bulk_limit(syms)
    else:
        try:
            quotes = await bt_service.get_all_quotes()  # use public API
            syms = [q["symbol"] for q in quotes]
        except Exception:
            syms = []

    out: list[dict] = []
    for s in syms:
        try:
            out.append(await port.get_position(s))
        except Exception:
            out.append({"symbol": s, "qty": 0.0, "avg_price": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0, "ts_ms": 0})
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
    if not _METRICS_OK:
        return {"entries": {}, "exits": {}, "open_positions": {}, "realized_pnl": {}}

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
