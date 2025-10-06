# app/pnl/repository.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.pnl_ledger import PnlLedger
from app.models.pnl_daily import PnlDaily
from .domain import PNLEventType, PNLLedgerEvent, ensure_utc


# ─────────────────────────────── Query helpers ───────────────────────────────

class Scope(TypedDict, total=False):
    exchange: str
    account_id: str
    symbol: str


def _apply_scope(query, scope: Optional[Scope]):
    if not scope:
        return query
    if scope.get("exchange"):
        query = query.where(PnlLedger.exchange == scope["exchange"])
    if scope.get("account_id"):
        query = query.where(PnlLedger.account_id == scope["account_id"])
    if scope.get("symbol"):
        query = query.where(PnlLedger.symbol == scope["symbol"])
    return query


def _apply_date_scope_daily(query, scope: Optional[Scope]):
    if not scope:
        return query
    if scope.get("exchange"):
        query = query.where(PnlDaily.exchange == scope["exchange"])
    if scope.get("account_id"):
        query = query.where(PnlDaily.account_id == scope["account_id"])
    if scope.get("symbol"):
        query = query.where(PnlDaily.symbol == scope["symbol"])
    return query


# ─────────────────────────────── Repository API ──────────────────────────────

def _find_existing_ledger(
    db: Session,
    *,
    exchange: str,
    account_id: str,
    symbol: str,
    event_type: str,
    ts_naive_utc: datetime,
    ref_trade_id: Optional[str],
    ref_order_id: Optional[str],
) -> Optional[PnlLedger]:
    """
    Tries to find an existing row using a natural unique key.
    Strategy:
      1) If ref_trade_id present → use it
      2) Else if ref_order_id present → use it
      3) Else fall back to (ts, event_type, symbol, exchange, account_id)
    """
    if ref_trade_id:
        q = (
            select(PnlLedger)
            .where(PnlLedger.exchange == exchange)
            .where(PnlLedger.account_id == account_id)
            .where(PnlLedger.symbol == symbol)
            .where(PnlLedger.event_type == event_type)
            .where(PnlLedger.ref_trade_id == ref_trade_id)
            .limit(1)
        )
        return db.execute(q).scalar_one_or_none()

    if ref_order_id:
        q = (
            select(PnlLedger)
            .where(PnlLedger.exchange == exchange)
            .where(PnlLedger.account_id == account_id)
            .where(PnlLedger.symbol == symbol)
            .where(PnlLedger.event_type == event_type)
            .where(PnlLedger.ref_order_id == ref_order_id)
            .limit(1)
        )
        return db.execute(q).scalar_one_or_none()

    q = (
        select(PnlLedger)
        .where(PnlLedger.exchange == exchange)
        .where(PnlLedger.account_id == account_id)
        .where(PnlLedger.symbol == symbol)
        .where(PnlLedger.event_type == event_type)
        .where(PnlLedger.ts == ts_naive_utc)
        .limit(1)
    )
    return db.execute(q).scalar_one_or_none()


def insert_ledger_event(db: Session, e: PNLLedgerEvent, *, dedupe: bool = True) -> PnlLedger:
    """
    Insert a realized-affecting event into pnl_ledger.
    - Keeps Decimal internally; convert to float only for API responses.
    - Stores timestamps as naive UTC (SQLite-friendly).
    - If dedupe=True, performs a pre-select to avoid duplicates.
    """
    ts_naive = ensure_utc(e.ts).replace(tzinfo=None)
    event_type_str = e.event_type.value if isinstance(e.event_type, PNLEventType) else str(e.event_type)

    if dedupe:
        existing = _find_existing_ledger(
            db,
            exchange=e.exchange,
            account_id=e.account_id,
            symbol=e.symbol,
            event_type=event_type_str,
            ts_naive_utc=ts_naive,
            ref_trade_id=e.ref_trade_id,
            ref_order_id=e.ref_order_id,
        )
        if existing:
            return existing

    row = PnlLedger(
        ts=ts_naive,
        exchange=e.exchange,
        account_id=e.account_id,
        symbol=e.symbol,
        base_asset=e.base_asset,
        quote_asset=e.quote_asset,
        event_type=event_type_str,
        amount_asset=Decimal(e.amount_asset),
        amount_usd=Decimal(e.amount_usd),
        ref_order_id=e.ref_order_id,
        ref_trade_id=e.ref_trade_id,
        meta=(e.meta or {}),
    )
    db.add(row)
    db.flush()  # get row.id without committing
    return row


def fetch_last_events(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    scope: Optional[Scope],
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Return latest ledger events in [start_utc, end_utc) for UI detail panels.
    Flattens common details from meta so the frontend can render columns without knowing schema.
    Output keys (some may be missing if not derivable):
      - time (ISO string), type, side, qty, price, fee_usd, pnl_delta (+ useful extras)
    """
    limit = max(1, min(500, int(limit)))

    start_naive = start_utc.replace(tzinfo=None)
    end_naive = end_utc.replace(tzinfo=None)

    q = (
        select(
            PnlLedger.ts,
            PnlLedger.event_type,
            PnlLedger.amount_usd,
            PnlLedger.amount_asset,
            PnlLedger.base_asset,
            PnlLedger.quote_asset,
            PnlLedger.meta,
        )
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
        .order_by(PnlLedger.ts.desc())
        .limit(limit)
    )
    q = _apply_scope(q, scope)

    rows = db.execute(q).all()
    out: List[Dict[str, Any]] = []

    def _fnum(v: Any) -> Optional[float]:
        try:
            n = float(v)
            if n != n or n in (float("inf"), float("-inf")):
                return None
            return n
        except Exception:
            return None

    for ts, event_type, amount_usd, amount_asset, base_asset, quote_asset, meta in rows:
        m = meta or {}

        side = m.get("side") or m.get("direction") or m.get("action") or m.get("taker_side") or m.get("maker_side")
        qty = (
            _fnum(m.get("qty"))
            or _fnum(m.get("quantity"))
            or _fnum(m.get("size"))
            or _fnum(m.get("amount"))
            or _fnum(m.get("filled_qty"))
            or _fnum(m.get("base_qty"))
            or _fnum(m.get("exec_qty"))
        )
        price = (
            _fnum(m.get("price"))
            or _fnum(m.get("avg_price"))
            or _fnum(m.get("fill_price"))
            or _fnum(m.get("mark"))
            or _fnum(m.get("exec_price"))
        )
        fee_usd = (
            _fnum(m.get("fee_usd"))
            or _fnum(m.get("commission_usd"))
            or _fnum(m.get("commission"))
            or _fnum(m.get("fee"))
        )
        if not fee_usd and event_type == "FEE":
            fee_usd = _fnum(amount_usd)

        out.append(
            {
                "time": ensure_utc(ts).isoformat().replace("+00:00", "Z") if isinstance(ts, datetime) else None,
                "ts": ts,
                "type": event_type,
                "symbol": scope.get("symbol") if isinstance(scope, dict) else None,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "side": side,
                "qty": qty,
                "price": price,
                "fee_usd": fee_usd,
                "pnl_delta": float(amount_usd or 0),
                "realized_usd": float(amount_usd or 0),
                "amount_asset": float(amount_asset or 0),
                "meta": m,
            }
        )
    return out


def aggregate_summary(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    scope: Optional[Scope] = None,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Compute total_usd and breakdowns by exchange and by symbol in the time window.
    Returns (total_usd, by_exchange[], by_symbol[]).
    """
    start_naive = start_utc.replace(tzinfo=None)
    end_naive = end_utc.replace(tzinfo=None)

    # Total
    q_total = (
        select(func.coalesce(func.sum(PnlLedger.amount_usd), 0))
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
    )
    q_total = _apply_scope(q_total, scope)
    (total_usd_dec,) = db.execute(q_total).one()
    total_usd = float(total_usd_dec or 0)

    # By exchange
    q_by_ex = (
        select(PnlLedger.exchange, func.coalesce(func.sum(PnlLedger.amount_usd), 0))
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
        .group_by(PnlLedger.exchange)
    )
    q_by_ex = _apply_scope(q_by_ex, scope)
    by_exchange = [{"exchange": ex, "total_usd": float(v or 0)} for ex, v in db.execute(q_by_ex).all()]

    # By symbol
    q_by_sym = (
        select(PnlLedger.exchange, PnlLedger.symbol, func.coalesce(func.sum(PnlLedger.amount_usd), 0))
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
        .group_by(PnlLedger.exchange, PnlLedger.symbol)
    )
    q_by_sym = _apply_scope(q_by_sym, scope)
    by_symbol = [{"exchange": ex, "symbol": sym, "total_usd": float(v or 0)} for ex, sym, v in db.execute(q_by_sym).all()]

    return total_usd, by_exchange, by_symbol


def aggregate_symbol_components(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    scope: Scope,
) -> Dict[str, float]:
    """
    Return component totals for a symbol scope: trade_realized, fees, funding, conversion.
    Scope must include at least symbol (and usually exchange/account_id).
    """
    start_naive = start_utc.replace(tzinfo=None)
    end_naive = end_utc.replace(tzinfo=None)

    q = (
        select(PnlLedger.event_type, func.coalesce(func.sum(PnlLedger.amount_usd), 0))
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
        .group_by(PnlLedger.event_type)
    )
    q = _apply_scope(q, scope)

    parts = {
        "TRADE_REALIZED": 0.0,
        "FEE": 0.0,
        "FUNDING": 0.0,
        "CONVERSION_PNL": 0.0,
    }
    for etype, v in db.execute(q).all():
        parts[etype] = float(v or 0)

    return {
        "trade_realized": parts["TRADE_REALIZED"],
        "fees": parts["FEE"],
        "funding": parts["FUNDING"],
        "conversion": parts["CONVERSION_PNL"],
    }


def fetch_daily_range(
    db: Session,
    start_date: date,
    end_date: date,
    scope: Optional[Scope] = None,
) -> List[PnlDaily]:
    """
    Return PnlDaily rows in [start_date, end_date] inclusive for the scope.
    """
    q = (
        select(PnlDaily)
        .where(PnlDaily.date >= start_date)
        .where(PnlDaily.date <= end_date)
        .order_by(PnlDaily.date.asc(), PnlDaily.exchange.asc(), PnlDaily.symbol.asc())
    )
    q = _apply_date_scope_daily(q, scope)
    return [row[0] for row in db.execute(q).all()]


def upsert_daily_row(
    db: Session,
    day: date,
    exchange: str,
    account_id: str,
    symbol: str,
    realized_usd: Decimal,
    fees_usd: Decimal,
) -> PnlDaily:
    """
    Idempotent upsert for a (date, exchange, account_id, symbol) record.
    Works on SQLite by doing get-or-create then update.
    """
    q = (
        select(PnlDaily)
        .where(PnlDaily.date == day)
        .where(PnlDaily.exchange == exchange)
        .where(PnlDaily.account_id == account_id)
        .where(PnlDaily.symbol == symbol)
        .limit(1)
    )
    existing = db.execute(q).scalar_one_or_none()
    if existing:
        existing.realized_usd = realized_usd
        existing.fees_usd = fees_usd
        db.flush()
        return existing

    row = PnlDaily(
        date=day,
        exchange=exchange,
        account_id=account_id,
        symbol=symbol,
        realized_usd=realized_usd,
        fees_usd=fees_usd,
    )
    db.add(row)
    db.flush()
    return row
