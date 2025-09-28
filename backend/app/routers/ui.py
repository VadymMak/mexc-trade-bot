# app/routers/ui.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from app.utils.symbols import ui_symbol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.session import get_db
from app.services.session_manager import SessionManager
from app.services.strategy_service import StrategyService
from app.execution.router import exec_router

from app.models.ui_state import UIState
from app.models.strategy_state import StrategyState  # noqa: F401

# NEW: persisted models for snapshot expansion
from app.models.orders import Order
from app.models.fills import Fill

router = APIRouter(prefix="/api/ui", tags=["ui"])

# ---- идемпотентность для UI-операций (отдельный неймспейс от /api/strategy) ----
_ui_idem = StrategyService(ttl_seconds=settings.idempotency_window_sec)

# ------- snapshot limits (keep responses lean; can move to settings later) -------
_SNAPSHOT_MAX_ORDERS = 200
_SNAPSHOT_MAX_FILLS = 500

# ────────────────────────────── Schemas ──────────────────────────────
class WatchlistPut(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class WatchlistPatch(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class LayoutPut(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class LayoutPatch(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class WatchlistBulkIn(BaseModel):
    symbols: List[str] = Field(default_factory=list, description="List of symbols to set as watchlist")

# ────────────────────────────── Helpers ──────────────────────────────
def _require_ui_state_enabled() -> None:
    if not settings.enable_ui_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="UI state persistence is disabled (ENABLE_UI_STATE=false).",
        )

def _etag(revision: int) -> str:
    return f'"{int(revision)}"'

def _norm_symbols(raw: List[str]) -> List[str]:
    # normalize + uppercase + unique, preserving order of first appearance
    out: List[str] = []
    seen = set()
    for s in raw or []:
        sym = ui_symbol(s)   # << normalize consistently
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out

def _get_watchlist_symbols(ui: UIState) -> List[str]:
    wl = ui.watchlist or {}
    syms = wl.get("symbols")
    if isinstance(syms, list):
        # normalize on read for stability
        return _norm_symbols([str(x) for x in syms])
    return []

def _check_if_match_or_412(current_revision: int, if_match: Optional[str]) -> None:
    if if_match is None:
        return
    try:
        expected = int(if_match.strip().strip('"'))
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid If-Match header. Use numeric revision, e.g. If-Match: "7"')
    if expected != int(current_revision):
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="Revision mismatch")

def _parse_include_param(value: Optional[str]) -> Tuple[bool, bool, bool]:
    """
    Returns (want_positions, want_orders, want_fills)
    Accepts comma-separated: e.g. positions,orders,fills
    """
    if not value:
        return (False, False, False)
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    return (
        "positions" in parts,
        "orders" in parts,
        "fills" in parts,
    )

async def _collect_positions_for_symbols(symbols: List[str]) -> List[Dict[str, Any]]:
    """Запрашиваем у исполнителя текущие позиции для списка символов (без ошибок на весь снапшот)."""
    port = exec_router.get_port(workspace_id=settings.workspace_id)
    out: List[Dict[str, Any]] = []
    for s in symbols:
        try:
            pos = await port.get_position(s)
            if isinstance(pos, dict):
                out.append(pos)
        except Exception:
            # пропускаем символ, если позиции нет или произошла ошибка
            pass
    return out

def _serialize_order(o: Order) -> Dict[str, Any]:
    return {
        "id": o.id,
        "workspace_id": o.workspace_id,
        "symbol": o.symbol,
        "side": o.side.value if hasattr(o.side, "value") else str(o.side),
        "type": o.type.value if hasattr(o.type, "value") else str(o.type),
        "tif": o.tif.value if hasattr(o.tif, "value") else str(o.tif),
        "qty": float(o.qty) if o.qty is not None else 0.0,
        "price": float(o.price) if o.price is not None else None,
        "filled_qty": float(o.filled_qty) if o.filled_qty is not None else 0.0,
        "avg_fill_price": float(o.avg_fill_price) if o.avg_fill_price is not None else None,
        "status": o.status.value if hasattr(o.status, "value") else str(o.status),
        "is_active": bool(o.is_active),
        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "last_event_at": o.last_event_at.isoformat() if o.last_event_at else None,
        "canceled_at": o.canceled_at.isoformat() if o.canceled_at else None,
        "strategy_tag": o.strategy_tag,
        "reduce_only": bool(o.reduce_only),
        "post_only": bool(o.post_only),
        "client_order_id": o.client_order_id,
        "exchange_order_id": o.exchange_order_id,
        "updated_at": o.updated_at.isoformat() if getattr(o, "updated_at", None) else None,
        "revision": int(getattr(o, "revision", 1) or 1),
    }

def _serialize_fill(f: Fill) -> Dict[str, Any]:
    return {
        "id": f.id,
        "workspace_id": f.workspace_id,
        "order_id": f.order_id,
        "symbol": f.symbol,
        "side": f.side.value if hasattr(f.side, "value") else str(f.side),
        "qty": float(f.qty) if f.qty is not None else 0.0,
        "price": float(f.price) if f.price is not None else 0.0,
        "quote_qty": float(f.quote_qty) if f.quote_qty is not None else None,
        "fee": float(f.fee) if f.fee is not None else 0.0,
        "fee_asset": f.fee_asset,
        "liquidity": f.liquidity.value if getattr(f, "liquidity", None) and hasattr(f.liquidity, "value") else (str(f.liquidity) if getattr(f, "liquidity", None) else None),
        "is_maker": bool(f.is_maker),
        "client_order_id": f.client_order_id,
        "exchange_order_id": f.exchange_order_id,
        "trade_id": f.trade_id,
        "executed_at": f.executed_at.isoformat() if f.executed_at else None,
        "strategy_tag": f.strategy_tag,
        "updated_at": f.updated_at.isoformat() if getattr(f, "updated_at", None) else None,
        "revision": int(getattr(f, "revision", 1) or 1),
    }

def _query_orders_and_fills(
    db: Session,
    workspace_id: int,
    symbols: Optional[List[str]],
) -> Tuple[List[Order], List[Fill]]:
    q_orders = db.query(Order).filter(Order.workspace_id == workspace_id)
    q_fills = db.query(Fill).filter(Fill.workspace_id == workspace_id)

    if symbols:
        normed = [ui_symbol(s) for s in symbols]   # << normalize before DB query
        q_orders = q_orders.filter(Order.symbol.in_(normed))
        q_fills = q_fills.filter(Fill.symbol.in_(normed))

    orders = q_orders.order_by(Order.id.desc()).limit(_SNAPSHOT_MAX_ORDERS).all()
    fills = q_fills.order_by(Fill.id.desc()).limit(_SNAPSHOT_MAX_FILLS).all()
    return orders, fills

# ────────────────────────────── Routes ──────────────────────────────
@router.get("/snapshot")
async def get_snapshot(
    response: Response,
    include: Optional[str] = Query(
        None,
        description='Опционально: "positions,orders,fills" — добавить соответствующие разделы',
    ),
    db: Session = Depends(get_db),
):
    """
    Единый снимок состояния для инициализации клиента.
    Базово возвращает:
      - ui_state (watchlist/layout/ui_prefs/revision/updated_at)
      - strategy_state (per_symbol/revision/updated_at)

    Если передано include=... (через запятую):
      - positions: "positions": [...] — живые позиции из исполнителя для символов в watchlist
      - orders:    "orders":   [...] — последние заказы из БД (фильтр по watchlist, если он есть)
      - fills:     "fills":    [...] — последние сделки из БД (фильтр по watchlist, если он есть)
    """
    _require_ui_state_enabled()
    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    snap = sm.get_snapshot()

    # выставляем ETag по ревизии UI
    ui_rev = snap.get("ui_state", {}).get("revision", 0)
    response.headers["ETag"] = _etag(ui_rev)

    want_pos, want_ord, want_fll = _parse_include_param(include)

    syms: List[str] = []
    if want_pos or want_ord or want_fll:
        ui = sm.ensure_ui_state()
        syms = _get_watchlist_symbols(ui)

    if want_pos:
        snap["positions"] = await _collect_positions_for_symbols(syms)

    if want_ord or want_fll:
        orders, fills = _query_orders_and_fills(db=db, workspace_id=settings.workspace_id, symbols=syms or None)
        if want_ord:
            snap["orders"] = [_serialize_order(o) for o in orders]
        if want_fll:
            snap["fills"] = [_serialize_fill(f) for f in fills]

    return snap

@router.post("/session/open")
def open_session(
    response: Response,
    reset: bool = Query(False, description="If true — clear UI/Strategy and start from scratch"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()
    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    snap = sm.open_new_session() if reset else sm.get_snapshot()
    ui_rev = snap.get("ui_state", {}).get("revision", 0)
    response.headers["ETag"] = _etag(ui_rev)
    return snap

# ── быстрый GET только списка символов ──────────────────────────────
@router.get("/watchlist")
def get_watchlist(response: Response, db: Session = Depends(get_db)):
    _require_ui_state_enabled()
    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    ui = sm.ensure_ui_state()
    syms = _get_watchlist_symbols(ui)
    response.headers["ETag"] = _etag(ui.revision)
    return {"symbols": syms, "revision": int(ui.revision)}

# ── основной bulk-роут для установки watchlist ──────────────────────
@router.post("/watchlist:bulk")
async def watchlist_bulk(
    payload: WatchlistBulkIn,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()

    # нормализация входа + лимит
    incoming = _norm_symbols(payload.symbols or [])
    if len(incoming) > settings.max_watchlist_bulk:
        raise HTTPException(
            status_code=400,
            detail=f"Too many symbols in bulk. Max allowed: {settings.max_watchlist_bulk}",
        )

    # идемпотентность по входному payload
    idem_payload = {"symbols": incoming}

    async def _apply() -> Dict[str, Any]:
        sm = SessionManager(db=db, workspace_id=settings.workspace_id)
        ui = sm.ensure_ui_state()
        # проверка ревизии
        _check_if_match_or_412(ui.revision, if_match)

        current = _get_watchlist_symbols(ui)
        changed = current != incoming

        if changed:
            ui.watchlist = {"symbols": incoming}
            ui.bump_revision()
            db.add(ui)
            db.commit()
            db.refresh(ui)

        # выставим заголовок ETag под текущую ревизию
        response.headers["ETag"] = _etag(ui.revision)
        return {
            "ok": True,
            "changed": bool(changed),
            "revision": int(ui.revision),
            "watchlist": {"symbols": _get_watchlist_symbols(ui)},
        }

    # если клиент не дал ключ — просто применяем действие
    if not x_idempotency_key:
        return await _apply()

    # иначе — идемпотентно (неймспейс операции ui.watchlist.bulk)
    result = await _ui_idem.execute_idempotent(
        op_name="ui.watchlist.bulk",
        idempotency_key=x_idempotency_key,
        payload=idem_payload,
        action=_apply,
    )

    # если был конфликт — это уже готовый JSON с ok:false
    if not result.get("ok", False):
        # ETag не меняем — он соответствует текущей ревизии в БД
        return result

    # при успешной операции выставим ETag результирующей ревизии (если _apply не успел)
    if "revision" in result and "ETag" not in response.headers:
        response.headers["ETag"] = _etag(int(result["revision"]))
    return result

# ── PUT/PATCH для бэкапов/совместимости (можно оставить как есть) ──
@router.put("/watchlist")
def put_watchlist(
    payload: WatchlistPut,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()
    # защита bulk — если тут symbols, тоже уважаем лимит
    syms = payload.data.get("symbols")
    if isinstance(syms, list) and len(syms) > settings.max_watchlist_bulk:
        raise HTTPException(
            status_code=400,
            detail=f"Too many symbols in bulk. Max allowed: {settings.max_watchlist_bulk}",
        )

    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    ui = sm.ensure_ui_state()
    _check_if_match_or_412(ui.revision, if_match)

    ui.watchlist = dict(payload.data or {})
    ui.bump_revision()
    db.add(ui)
    db.commit()
    db.refresh(ui)

    response.headers["ETag"] = _etag(ui.revision)
    return {"revision": int(ui.revision), "ui_state": ui.to_dict()}

@router.patch("/watchlist")
def patch_watchlist(
    payload: WatchlistPatch,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()
    syms = payload.data.get("symbols")
    if isinstance(syms, list) and len(syms) > settings.max_watchlist_bulk:
        raise HTTPException(
            status_code=400,
            detail=f"Too many symbols in bulk. Max allowed: {settings.max_watchlist_bulk}",
        )

    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    ui = sm.ensure_ui_state()
    _check_if_match_or_412(ui.revision, if_match)

    dst = dict(ui.watchlist or {})
    dst.update(dict(payload.data or {}))
    ui.watchlist = dst

    ui.bump_revision()
    db.add(ui)
    db.commit()
    db.refresh(ui)

    response.headers["ETag"] = _etag(ui.revision)
    return {"revision": int(ui.revision), "ui_state": ui.to_dict()}

@router.put("/layout")
def put_layout(
    payload: LayoutPut,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()
    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    ui = sm.ensure_ui_state()
    _check_if_match_or_412(ui.revision, if_match)

    ui.layout = dict(payload.data or {})
    ui.bump_revision()
    db.add(ui)
    db.commit()
    db.refresh(ui)

    response.headers["ETag"] = _etag(ui.revision)
    return {"revision": int(ui.revision), "ui_state": ui.to_dict()}

@router.patch("/layout")
def patch_layout(
    payload: LayoutPatch,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    _require_ui_state_enabled()
    sm = SessionManager(db=db, workspace_id=settings.workspace_id)
    ui = sm.ensure_ui_state()
    _check_if_match_or_412(ui.revision, if_match)

    dst = dict(ui.layout or {})
    dst.update(dict(payload.data or {}))
    ui.layout = dst

    ui.bump_revision()
    db.add(ui)
    db.commit()
    db.refresh(ui)

    response.headers["ETag"] = _etag(ui.revision)
    return {"revision": int(ui.revision), "ui_state": ui.to_dict()}
