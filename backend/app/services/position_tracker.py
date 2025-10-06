# app/services/position_tracker.py
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

from sqlalchemy.orm import Session

from app.models.positions import Position
from app.models.fills import Fill

try:
    from app.models.pnl_ledger import PnLLedger  # type: ignore
except Exception:  # pragma: no cover
    PnLLedger = None  # type: ignore


@dataclass
class TrackerResult:
    symbol: str
    qty_after: float
    avg_after: float
    realized_pnl_delta: float
    realized_pnl_cum: float


# ───────────────────────────── helpers ─────────────────────────────

def _num(x: Any, dflt: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return float(dflt)


def _get_side_str(side: Any) -> str:
    s = str(getattr(side, "value", side)).upper()
    return "BUY" if s == "BUY" else "SELL"


def _now_ts() -> int:
    return int(time.time())


def _model_columns(model_cls) -> set[str]:
    try:
        return set(model_cls.__table__.columns.keys())  # type: ignore[attr-defined]
    except Exception:
        return set()


def _filtered_kwargs(model_cls, **kwargs) -> Dict[str, Any]:
    cols = _model_columns(model_cls)
    return {k: v for k, v in kwargs.items() if k in cols}


# ───────────────────────────── tracker ─────────────────────────────

class PositionTracker:
    """
    Durable position & realized PnL tracker (per workspace_id, symbol).

    • **Spot-only, long-only invariant**:
        - Never create negative qty (short).
        - SELL is capped to current long quantity.
        - If a negative qty is somehow stored (legacy), BUY first closes the short,
          then opens long only with the remainder.

    • Idempotency: (exchange_order_id, trade_id) tuple in-memory
      + optional DB guard via pnl_ledger (if present).

    • Fees: realized PnL is reduced by the provided fee amount (if any).
    """

    _IDEM_CACHE_LIMIT = 10_000

    def __init__(self, db: Session, workspace_id: int) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self._idem_seen: set[Tuple[Optional[str], Optional[str]]] = set()

    # ─────────────── public API ───────────────

    def on_fill(self, fill: Fill | Dict[str, Any] | None = None, **kwargs) -> TrackerResult:
        """
        Accepts either:
          - ORM Fill instance
          - dict with fill-like fields
          - keyword args: symbol, side, qty, price, ts_ms?, trade_id?, exchange_order_id?, fee?, fee_asset?, strategy_tag?
        """
        if fill is None and kwargs:
            f: Dict[str, Any] = {
                "symbol": str(kwargs.get("symbol", "")).strip().upper(),
                "side": _get_side_str(kwargs.get("side", "SELL")),
                "qty": _num(kwargs.get("qty", 0.0)),
                "price": _num(kwargs.get("price", 0.0)),
                "fee": _num(kwargs.get("fee", 0.0)),
                "fee_asset": kwargs.get("fee_asset"),
                "client_order_id": kwargs.get("client_order_id"),
                "exchange_order_id": kwargs.get("exchange_order_id"),
                # prefer explicit trade_id; else use ts_ms as a stable per-fill id; else None
                "trade_id": kwargs.get("trade_id")
                    or (str(kwargs.get("ts_ms")) if kwargs.get("ts_ms") is not None else None),
                "executed_at": kwargs.get("executed_at"),
                "strategy_tag": kwargs.get("strategy_tag"),
                "exchange": kwargs.get("exchange"),
                "account_id": kwargs.get("account_id"),
            }
        else:
            f = self._coerce_fill(fill)  # type: ignore[arg-type]

        key = (f.get("exchange_order_id"), f.get("trade_id") or f.get("id"))

        # Fast in-memory idempotency
        if key in self._idem_seen:
            return self._snapshot(f["symbol"])

        # Defensive DB idempotency (if pnl_ledger exists)
        if PnLLedger is not None and f.get("trade_id"):
            exists = (
                self.db.query(PnLLedger)  # type: ignore
                .filter(
                    PnLLedger.workspace_id == self.workspace_id,  # type: ignore[attr-defined]
                    PnLLedger.symbol == f["symbol"],              # type: ignore[attr-defined]
                    PnLLedger.trade_id == str(f["trade_id"]),     # type: ignore[attr-defined]
                )
                .first()
            )
            if exists:
                self._idem_seen_add(key)
                return self._snapshot(f["symbol"])

        # Apply and persist
        res = self._apply_fill(f)

        # Append ledger (best-effort)
        self._append_ledger_row(f, res.realized_pnl_delta)

        # Remember key
        self._idem_seen_add(key)
        return res

    def rebuild_from_fills(self) -> None:
        """Rebuild all Position rows for this workspace by replaying fills in chronological order."""
        self.db.query(Position).filter(Position.workspace_id == self.workspace_id).delete()
        self.db.commit()

        fills = (
            self.db.query(Fill)
            .filter(Fill.workspace_id == self.workspace_id)
            .order_by(Fill.executed_at.asc().nullsfirst(), Fill.id.asc())
            .all()
        )
        for fl in fills:
            self.on_fill(fl)

    # ─────────────── internals ───────────────

    def _coerce_fill(self, fill: Fill | Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(fill, dict):
            d = dict(fill)
        else:
            d = {
                "id": getattr(fill, "id", None),
                "workspace_id": getattr(fill, "workspace_id", None),
                "order_id": getattr(fill, "order_id", None),
                "symbol": getattr(fill, "symbol", "") or "",
                "side": _get_side_str(getattr(fill, "side", "SELL")),
                "qty": _num(getattr(fill, "qty", 0.0)),
                "price": _num(getattr(fill, "price", 0.0)),
                "quote_qty": _num(getattr(fill, "quote_qty", 0.0)),
                "fee": _num(getattr(fill, "fee", 0.0)),
                "fee_asset": getattr(fill, "fee_asset", None),
                "client_order_id": getattr(fill, "client_order_id", None),
                "exchange_order_id": getattr(fill, "exchange_order_id", None),
                "trade_id": getattr(fill, "trade_id", None),
                "executed_at": getattr(fill, "executed_at", None),
                "strategy_tag": getattr(fill, "strategy_tag", None),
                "exchange": getattr(fill, "exchange", None),
                "account_id": getattr(fill, "account_id", None),
            }

        d["symbol"] = str(d.get("symbol", "")).strip().upper()
        d["side"] = _get_side_str(d.get("side", "SELL"))
        d["qty"] = _num(d.get("qty", 0.0))
        d["price"] = _num(d.get("price", 0.0))
        d["fee"] = _num(d.get("fee", 0.0))
        return d

    def _idem_seen_add(self, key: Tuple[Optional[str], Optional[str]]) -> None:
        self._idem_seen.add(key)
        if len(self._idem_seen) > self._IDEM_CACHE_LIMIT:
            self._idem_seen.clear()

    def _load_or_create_position(self, symbol: str) -> Position:
        pos = (
            self.db.query(Position)
            .filter(Position.workspace_id == self.workspace_id, Position.symbol == symbol)
            .with_for_update(read=False, nowait=False)
            .first()
        )
        if pos:
            return pos

        pos = Position(  # type: ignore[call-arg]
            workspace_id=self.workspace_id,
            symbol=symbol,
            qty=0.0,
            avg_price=0.0,
            realized_pnl=0.0,
        )
        if hasattr(pos, "realized_pnl_cum") and not hasattr(pos, "realized_pnl"):
            setattr(pos, "realized_pnl_cum", 0.0)
        self.db.add(pos)
        self.db.flush()
        return pos

    def _get_pos_fields(self, pos: Position) -> Tuple[float, float, float]:
        qty = _num(getattr(pos, "qty", 0.0))
        avg = _num(getattr(pos, "avg_price", getattr(pos, "avg", 0.0)))
        realized_cum = _num(getattr(pos, "realized_pnl", getattr(pos, "realized_pnl_cum", 0.0)))
        return qty, avg, realized_cum

    def _set_pos_fields(self, pos: Position, qty: float, avg: float, realized_cum: float) -> None:
        if hasattr(pos, "qty"):
            setattr(pos, "qty", qty)
        if hasattr(pos, "avg_price"):
            setattr(pos, "avg_price", avg)
        elif hasattr(pos, "avg"):
            setattr(pos, "avg", avg)
        if hasattr(pos, "realized_pnl"):
            setattr(pos, "realized_pnl", realized_cum)
        elif hasattr(pos, "realized_pnl_cum"):
            setattr(pos, "realized_pnl_cum", realized_cum)
        if hasattr(pos, "updated_at"):
            try:
                from datetime import datetime, timezone
                setattr(pos, "updated_at", datetime.now(tz=timezone.utc).replace(tzinfo=None))
            except Exception:
                pass

    def _apply_fill(self, f: Dict[str, Any]) -> TrackerResult:
        """
        Core inventory math with **spot long-only** enforcement.
        - SELL is limited to current long qty (extra ignored).
        - BUY can flip a legacy short to flat, then open long with the remainder.
        """
        symbol = f["symbol"]
        side = f["side"]
        qty = max(0.0, _num(f["qty"]))       # never negative
        price = max(0.0, _num(f["price"]))   # never negative
        fee = max(0.0, _num(f.get("fee", 0.0)))

        pos = self._load_or_create_position(symbol)
        cur_qty, cur_avg, realized_cum = self._get_pos_fields(pos)

        realized_delta = 0.0

        if side == "BUY":
            if cur_qty < 0:
                # Close legacy short first (best-effort), then open long with remainder.
                close = min(qty, -cur_qty)
                realized_delta += (cur_avg - price) * close  # closing short PnL
                qty_after_close = qty - close
                cur_qty += close  # moves toward 0
                if math.isclose(cur_qty, 0.0, abs_tol=1e-12):
                    cur_qty = 0.0
                    cur_avg = 0.0
                if qty_after_close > 0:
                    # open long with remaining
                    if cur_qty <= 0:
                        cur_avg = price
                        cur_qty = qty_after_close
                    else:
                        # (shouldn't happen under long-only, but safe)
                        new_qty = cur_qty + qty_after_close
                        cur_avg = (cur_qty * cur_avg + qty_after_close * price) / new_qty if new_qty > 0 else 0.0
                        cur_qty = new_qty
            else:
                # Normal long add
                new_qty = cur_qty + qty
                cur_avg = (cur_qty * cur_avg + qty * price) / new_qty if new_qty > 0 else 0.0
                cur_qty = new_qty

            realized_delta -= fee  # subtract fee from realized

        else:  # SELL
            if cur_qty <= 0:
                # No long inventory → ignore sell (spot long-only)
                qty_to_sell = 0.0
            else:
                qty_to_sell = min(qty, cur_qty)

            if qty_to_sell > 0:
                realized_delta += (price - cur_avg) * qty_to_sell
                cur_qty = cur_qty - qty_to_sell
                if math.isclose(cur_qty, 0.0, abs_tol=1e-12):
                    cur_qty = 0.0
                    cur_avg = 0.0
            # Any "excess" sell is ignored to prevent shorts.
            realized_delta -= fee  # subtract fee from realized

        realized_cum += realized_delta

        self._set_pos_fields(pos, qty=cur_qty, avg=cur_avg, realized_cum=realized_cum)
        self.db.add(pos)
        self.db.commit()
        self.db.refresh(pos)

        return TrackerResult(
            symbol=symbol,
            qty_after=cur_qty,
            avg_after=cur_avg,
            realized_pnl_delta=realized_delta,
            realized_pnl_cum=realized_cum,
        )

    def _append_ledger_row(self, f: Dict[str, Any], realized_delta: float) -> None:
        if PnLLedger is None:
            return
        data = {
            "workspace_id": self.workspace_id,
            "symbol": f.get("symbol"),
            "side": f.get("side"),
            "qty": _num(f.get("qty")),
            "price": _num(f.get("price")),
            "fee": _num(f.get("fee")),
            "fee_asset": f.get("fee_asset"),
            "realized_pnl": realized_delta,
            "executed_at": f.get("executed_at"),
            "trade_id": f.get("trade_id") or str(f.get("id")),
            "exchange_order_id": f.get("exchange_order_id"),
            "client_order_id": f.get("client_order_id"),
            "strategy_tag": f.get("strategy_tag"),
            # Optional enrichers:
            "exchange": f.get("exchange"),
            "account_id": f.get("account_id"),
            "created_ts": _now_ts(),
        }
        row_kwargs = _filtered_kwargs(PnLLedger, **data)  # type: ignore
        try:
            row = PnLLedger(**row_kwargs)  # type: ignore
            self.db.add(row)
            self.db.commit()
        except Exception:
            self.db.rollback()

    def _snapshot(self, symbol: str) -> TrackerResult:
        pos = (
            self.db.query(Position)
            .filter(Position.workspace_id == self.workspace_id, Position.symbol == symbol)
            .first()
        )
        if not pos:
            return TrackerResult(symbol=symbol, qty_after=0.0, avg_after=0.0,
                                 realized_pnl_delta=0.0, realized_pnl_cum=0.0)
        q, a, r = self._get_pos_fields(pos)
        return TrackerResult(symbol=symbol, qty_after=q, avg_after=a,
                             realized_pnl_delta=0.0, realized_pnl_cum=r)
