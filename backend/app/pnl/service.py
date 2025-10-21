# app/pnl/service.py
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple, List

from sqlalchemy.orm import Session

from .domain import (
    PNLEventType,
    PNLLedgerEvent,
    PNLPeriod,
    PnlSummary,
    PnlSymbolDetail,
    ensure_utc,
    period_window,
)
from . import repository as repo

# Optional SSE publisher (non-fatal if absent)
try:  # pragma: no cover
    from app.services.sse_publisher import publish as sse_publish  # publish(event_type:str, payload:dict) -> None
except Exception:  # pragma: no cover
    sse_publish = None  # type: ignore


# ─────────────────────────────── Helpers ───────────────────────────────

def _to_decimal_str(x: Decimal | str | float | int) -> str:
    """Convert numeric input to a canonical Decimal string (no FP drift)."""
    if isinstance(x, Decimal):
        return str(x)
    try:
        return str(Decimal(str(x)))
    except (InvalidOperation, ValueError, TypeError):
        # Last-resort fallback (keeps service resilient even on odd inputs)
        return str(x)


def _emit_pnl_tick(payload: Dict[str, Any]) -> None:
    """Fire-and-forget SSE event 'pnl_tick' if publisher is present."""
    if sse_publish is not None:
        try:
            sse_publish("pnl_tick", payload)
        except Exception:
            # Never break accounting on SSE failure
            pass


def _isoz(ts: datetime) -> str:
    """UTC → RFC3339 with 'Z' suffix."""
    return ensure_utc(ts).isoformat().replace("+00:00", "Z")


def _fnum(x: Any, dflt: float = 0.0) -> float:
    """Coerce to finite float; return dflt on NaN/Inf/Bad."""
    try:
        v = float(x)
        if v == v and v not in (float("inf"), float("-inf")):
            return v
    except Exception:
        pass
    return float(dflt)


def _maybe_parse_ts(ts_like: Any) -> Optional[str]:
    """
    Parse a variety of timestamp shapes into ISO 'Z' string:
      - datetime
      - epoch seconds or millis
      - iso string (returned as-is if looks like one)
    """
    if isinstance(ts_like, datetime):
        return _isoz(ts_like)
    if isinstance(ts_like, (int, float)) and ts_like > 0:
        try:
            # ms vs s by magnitude
            base = float(ts_like) / (1000.0 if ts_like > 3_000_000_000 else 1.0)
            return _isoz(datetime.fromtimestamp(base, tz=timezone.utc))
        except Exception:
            return None
    if isinstance(ts_like, str) and ts_like:
        # if it's already an ISO-like string, let UI display it
        return ts_like
    return None


def _normalize_components(parts: Dict[str, Any]) -> Dict[str, float]:
    """
    Map repository parts to a UI-friendly dict with useful aliases.
    Expected 'parts' keys from repo: trade_realized, fees, funding, conversion.
    """
    realized = _fnum(parts.get("trade_realized"), 0.0)
    fees = _fnum(parts.get("fees"), 0.0)
    funding = _fnum(parts.get("funding"), 0.0)
    conversion = _fnum(parts.get("conversion"), 0.0)

    # Provide common aliases the frontend reads
    return {
        # aliases for realized
        "realized": realized,
        "realized_usd": realized,
        "rpnl": realized,

        # fees under several names (keep sign as-is)
        "fees": fees,
        "fees_usd": fees,
        "commission": fees,
        "commission_usd": fees,

        # extra components
        "funding": funding,
        "conversion": conversion,

        # we don't compute mark-to-market here
        "unrealized": 0.0,
    }


def _normalize_event_row(raw: Any) -> Dict[str, Any]:
    """
    Best-effort normalization for 'last_events' rows so the modal shows useful columns.
    Accepts dict-like or model-like objects.

    It digs into `meta` to populate side/qty/price/fee/ids when absent at top level.
    """
    def _get(o: Any, *keys: str) -> Any:
        if isinstance(o, dict):
            for k in keys:
                if k in o:
                    return o[k]
        else:
            for k in keys:
                if hasattr(o, k):
                    return getattr(o, k)
        return None

    def _from_meta(meta: Any, *keys: str) -> Any:
        if not meta:
            return None
        if isinstance(meta, dict):
            for k in keys:
                if k in meta:
                    return meta[k]
        # model-like with .meta
        if hasattr(meta, "__dict__"):
            m = getattr(meta, "__dict__", {})
            for k in keys:
                if k in m:
                    return m[k]
        return None

    # Extract timestamp; prefer explicit executed_at if present
    meta = _get(raw, "meta")
    ts = (
        _get(raw, "executed_at")
        or _from_meta(meta, "executed_at")
        or _get(raw, "ts", "timestamp", "created_at", "time")
    )
    time_val = _maybe_parse_ts(ts)

    event_type = _get(raw, "event_type", "type", "kind", "category")

    # Try top-level first…
    side = _get(raw, "side", "direction", "action", "taker_side", "maker_side")
    qty = _get(raw, "qty", "quantity", "size", "amount", "base_qty", "exec_qty")
    price = _get(raw, "price", "avg_price", "fill_price", "mark", "exec_price")
    fee = _get(raw, "fee", "fee_usd", "commission", "commission_usd")
    fee_asset = _get(raw, "fee_asset", "feeAsset")
    pnl_delta = _get(raw, "pnl_delta", "delta_usd", "realized_delta_usd", "amount_usd")

    client_order_id = _get(raw, "client_order_id", "clientOrderId")
    exchange_order_id = _get(raw, "exchange_order_id", "order_id", "orderId")
    trade_id = _get(raw, "trade_id", "tradeId")

    # …and then meta fallbacks
    if side is None:
        side = _from_meta(meta, "side")
    if qty is None:
        qty = _from_meta(meta, "qty", "quantity", "size", "base_qty")
    if price is None:
        price = _from_meta(meta, "price", "fill_price", "exec_price")
    if fee is None:
        fee = _from_meta(meta, "fee", "fee_usd", "commission")
    if fee_asset is None:
        fee_asset = _from_meta(meta, "fee_asset", "feeAsset")
    if client_order_id is None:
        client_order_id = _from_meta(meta, "client_order_id", "clientOrderId")
    if exchange_order_id is None:
        exchange_order_id = _from_meta(meta, "exchange_order_id", "order_id", "orderId")
    if trade_id is None:
        trade_id = _from_meta(meta, "trade_id", "tradeId")

    row = {
        "time": time_val,
        "type": str(event_type) if event_type is not None else None,
        "side": side if side is not None else None,
        "qty": _fnum(qty, 0.0) if qty is not None else None,
        "price": _fnum(price, 0.0) if price is not None else None,
        "fee": _fnum(fee, 0.0) if fee is not None else None,
        "fee_asset": str(fee_asset) if fee_asset is not None else None,
        "pnl_delta": _fnum(pnl_delta, 0.0) if pnl_delta is not None else None,
        # order/fill identifiers (useful for drill-down)
        "client_order_id": client_order_id,
        "exchange_order_id": exchange_order_id,
        "trade_id": trade_id,
        # keep some originals in case UI extends columns later
        "amount_asset": _get(raw, "amount_asset"),
        "amount_usd": _get(raw, "amount_usd"),
    }
    # prune Nones to reduce payload noise
    return {k: v for k, v in row.items() if v is not None}


# ─────────────────────────────── Service API ───────────────────────────────

class PnlService:
    """
    High-level PnL service:
    - Emits realized-affecting events to the ledger
    - Fetches summaries/symbol details/daily history for UI
    - Computes period windows (today/WTD/MTD/custom)
    - Emits SSE 'pnl_tick' after successful ledger writes
    """

    def normalize_event(
        self,
        *,
        event_type: PNLEventType,
        amount_asset: Decimal,
        amount_usd: Optional[Decimal],
        base_asset: str,
        quote_asset: str,
        price_usd: Optional[Decimal] = None,
    ) -> Tuple[Decimal, Decimal]:
        if amount_usd is not None:
            return amount_asset, amount_usd
        if price_usd is not None:
            return amount_asset, (amount_asset * price_usd)
        if quote_asset.upper() in {"USDT", "USDC", "FDUSD", "BUSD"}:
            return amount_asset, amount_asset
        raise ValueError("normalize_event requires amount_usd or price_usd when quote_asset is not a stablecoin")

    # ── Write operations ──────────────────────────────────────────────────
    def log_trade_realized(self, db: Session, *, ts: datetime, exchange: str, account_id: str,
                           symbol: str, base_asset: str, quote_asset: str,
                           realized_asset: Decimal, realized_usd: Optional[Decimal] = None,
                           price_usd: Optional[Decimal] = None, ref_order_id: Optional[str] = None,
                           ref_trade_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None,
                           emit_sse: bool = True) -> None:
        amt_asset, amt_usd = self.normalize_event(
            event_type=PNLEventType.TRADE_REALIZED,
            amount_asset=realized_asset,
            amount_usd=realized_usd,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_usd=price_usd,
        )
        event = PNLLedgerEvent(
            ts=ensure_utc(ts),
            exchange=exchange,
            account_id=account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            event_type=PNLEventType.TRADE_REALIZED,
            amount_asset=_to_decimal_str(amt_asset),
            amount_usd=_to_decimal_str(amt_usd),
            ref_order_id=ref_order_id,
            ref_trade_id=ref_trade_id,
            meta=meta or {},
        )
        repo.insert_ledger_event(db, event, dedupe=True)
        if emit_sse:
            try:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "delta_usd": float(amt_usd), "event_type": "TRADE_REALIZED"
                })
            except Exception:
                # keep robust even if float() fails
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "event_type": "TRADE_REALIZED"
                })

    def log_fee(self, db: Session, *, ts: datetime, exchange: str, account_id: str,
                symbol: str, base_asset: str, quote_asset: str, fee_asset_delta: Decimal,
                fee_usd: Optional[Decimal] = None, price_usd: Optional[Decimal] = None,
                ref_order_id: Optional[str] = None, ref_trade_id: Optional[str] = None,
                meta: Optional[Dict[str, Any]] = None, emit_sse: bool = True) -> None:
        amt_asset, amt_usd = self.normalize_event(
            event_type=PNLEventType.FEE,
            amount_asset=fee_asset_delta,
            amount_usd=fee_usd,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_usd=price_usd,
        )
        event = PNLLedgerEvent(
            ts=ensure_utc(ts),
            exchange=exchange,
            account_id=account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            event_type=PNLEventType.FEE,
            amount_asset=_to_decimal_str(amt_asset),
            amount_usd=_to_decimal_str(amt_usd),
            ref_order_id=ref_order_id,
            ref_trade_id=ref_trade_id,
            meta=meta or {},
        )
        repo.insert_ledger_event(db, event, dedupe=True)
        if emit_sse:
            try:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "delta_usd": float(amt_usd), "event_type": "FEE"
                })
            except Exception:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "event_type": "FEE"
                })

    def log_funding(self, db: Session, *, ts: datetime, exchange: str, account_id: str,
                    symbol: str, base_asset: str, quote_asset: str, funding_asset_delta: Decimal,
                    funding_usd: Optional[Decimal] = None, price_usd: Optional[Decimal] = None,
                    ref_order_id: Optional[Decimal] = None, ref_trade_id: Optional[str] = None,
                    meta: Optional[Dict[str, Any]] = None, emit_sse: bool = True) -> None:
        amt_asset, amt_usd = self.normalize_event(
            event_type=PNLEventType.FUNDING,
            amount_asset=funding_asset_delta,
            amount_usd=funding_usd,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_usd=price_usd,
        )
        event = PNLLedgerEvent(
            ts=ensure_utc(ts),
            exchange=exchange,
            account_id=account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            event_type=PNLEventType.FUNDING,
            amount_asset=_to_decimal_str(amt_asset),
            amount_usd=_to_decimal_str(amt_usd),
            ref_order_id=str(ref_order_id) if ref_order_id is not None else None,
            ref_trade_id=ref_trade_id,
            meta=meta or {},
        )
        repo.insert_ledger_event(db, event, dedupe=True)
        if emit_sse:
            try:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "delta_usd": float(amt_usd), "event_type": "FUNDING"
                })
            except Exception:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "event_type": "FUNDING"
                })

    def log_conversion_pnl(self, db: Session, *, ts: datetime, exchange: str, account_id: str,
                           symbol: str, base_asset: str, quote_asset: str, conversion_asset_delta: Decimal,
                           conversion_usd: Optional[Decimal] = None, price_usd: Optional[Decimal] = None,
                           ref_order_id: Optional[str] = None, ref_trade_id: Optional[str] = None,
                           meta: Optional[Dict[str, Any]] = None, emit_sse: bool = True) -> None:
        amt_asset, amt_usd = self.normalize_event(
            event_type=PNLEventType.CONVERSION_PNL,
            amount_asset=conversion_asset_delta,
            amount_usd=conversion_usd,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_usd=price_usd,
        )
        event = PNLLedgerEvent(
            ts=ensure_utc(ts),
            exchange=exchange,
            account_id=account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            event_type=PNLEventType.CONVERSION_PNL,
            amount_asset=_to_decimal_str(amt_asset),
            amount_usd=_to_decimal_str(amt_usd),
            ref_order_id=ref_order_id,
            ref_trade_id=ref_trade_id,
            meta=meta or {},
        )
        repo.insert_ledger_event(db, event, dedupe=True)
        if emit_sse:
            try:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "delta_usd": float(amt_usd), "event_type": "CONVERSION_PNL"
                })
            except Exception:
                _emit_pnl_tick({
                    "exchange": exchange, "account_id": account_id, "symbol": symbol,
                    "ts": _isoz(ts), "event_type": "CONVERSION_PNL"
                })

    # ── Read operations ───────────────────────────────────────────────────

    def get_summary(
        self,
        db: Session,
        *,
        period: PNLPeriod,
        tz: Optional[str] = None,
        scope: Optional[repo.Scope] = None,
        now: Optional[datetime] = None,
    ) -> PnlSummary:
        from app.models.trades import Trade
        
        start_utc, end_utc = period_window(period, tz=tz, now=now)
        
        # ═══════════════════════════════════════════════════════════
        # НОВАЯ ЛОГИКА: Читаем из trades таблицы
        # ═══════════════════════════════════════════════════════════
        
        # Базовый запрос к закрытым сделкам
        query = db.query(Trade).filter(
            Trade.entry_time >= start_utc,
            Trade.entry_time < end_utc,
            Trade.status == 'CLOSED',
            Trade.exit_time.isnot(None)
        )
        
        # Применить scope фильтры если указаны
        if scope:
            if 'exchange' in scope:
                query = query.filter(Trade.exchange == scope['exchange'])
            if 'account_id' in scope:
                query = query.filter(Trade.account_id == scope['account_id'])
            if 'symbol' in scope:
                query = query.filter(Trade.symbol == scope['symbol'])
        
        # Получить все сделки
        trades = query.all()
        
        # Подсчитать общий P&L (GROSS - до вычета комиссий)
        total_usd = 0.0
        for t in trades:
            if t.entry_price and t.exit_price and t.entry_qty:
                # Для лонгов (BUY → SELL)
                if t.entry_side == "BUY":
                    pnl_per_unit = t.exit_price - t.entry_price
                else:
                    # Для шортов (SELL → BUY)
                    pnl_per_unit = t.entry_price - t.exit_price
                
                gross_pnl = pnl_per_unit * t.entry_qty
                total_usd += gross_pnl
        
        # Группировка по биржам
        by_exchange = {}
        for t in trades:
            ex = t.exchange or 'UNKNOWN'
            if ex not in by_exchange:
                by_exchange[ex] = 0.0
            
            # Рассчитать gross P&L для этой сделки
            if t.entry_price and t.exit_price and t.entry_qty:
                if t.entry_side == "BUY":
                    pnl_per_unit = t.exit_price - t.entry_price
                else:
                    pnl_per_unit = t.entry_price - t.exit_price
                
                gross_pnl = pnl_per_unit * t.entry_qty
                by_exchange[ex] += gross_pnl
        
        # Преобразовать в формат списка
        by_exchange_list = [
            {"exchange": ex, "total_usd": usd}
            for ex, usd in by_exchange.items()
        ]
        
        # Группировка по символам
        by_symbol_dict = {}
        for t in trades:
            key = (t.exchange or 'UNKNOWN', t.symbol)
            if key not in by_symbol_dict:
                by_symbol_dict[key] = 0.0
            
            # Рассчитать gross P&L для этой сделки
            if t.entry_price and t.exit_price and t.entry_qty:
                if t.entry_side == "BUY":
                    pnl_per_unit = t.exit_price - t.entry_price
                else:
                    pnl_per_unit = t.entry_price - t.exit_price
                
                gross_pnl = pnl_per_unit * t.entry_qty
                by_symbol_dict[key] += gross_pnl
        
        # Преобразовать в формат списка
        by_symbol_list = [
            {"exchange": ex, "symbol": sym, "total_usd": usd}
            for (ex, sym), usd in by_symbol_dict.items()
        ]
        
        # ═══════════════════════════════════════════════════════════
        
        return PnlSummary(
            period=period,
            total_usd=total_usd,
            by_exchange=by_exchange_list,
            by_symbol=by_symbol_list,
        )

    def get_symbol_detail(
        self,
        db: Session,
        *,
        symbol: str,
        exchange: Optional[str] = None,
        account_id: Optional[str] = None,
        period: PNLPeriod = "today",
        tz: Optional[str] = None,
        events_limit: int = 50,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Per-symbol totals + component breakdown + recent events."""
        start_utc, end_utc = period_window(period, tz=tz, now=now)

        scope: repo.Scope = {"symbol": symbol}
        if exchange:
            scope["exchange"] = exchange
        if account_id:
            scope["account_id"] = account_id

        parts = repo.aggregate_symbol_components(db, start_utc, end_utc, scope)
        last_events_raw = repo.fetch_last_events(db, start_utc, end_utc, scope, limit=events_limit)

        comps = _normalize_components(parts)
        total_usd = float(comps["realized"] + comps["fees"] + comps["funding"] + comps["conversion"])

        # normalize events for the UI (now meta-aware)
        last_events: List[Dict[str, Any]] = []
        for e in last_events_raw:
            try:
                last_events.append(_normalize_event_row(e))
            except Exception:
                # never break the response—add minimal safe row
                last_events.append({"type": "EVENT", "time": None})

        return {
            "symbol": symbol,
            "exchange": exchange or "",
            "account_id": account_id or "",
            "total_usd": total_usd,
            "components": comps,
            "last_events": last_events,
        }

    def get_daily_history(
        self,
        db: Session,
        *,
        period: PNLPeriod,
        tz: Optional[str] = None,
        scope: Optional[repo.Scope] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns daily buckets for charts from pnl_daily over the computed window.
        Output: [{ "date": "YYYY-MM-DD", "exchange": "...", "symbol": "...", "realized_usd": 0.0, "fees_usd": 0.0 }]
        """
        start_utc, end_utc = period_window(period, tz=tz, now=now)
        # Convert to dates inclusive (UTC-based)
        start_day: date = ensure_utc(start_utc).date()
        end_day: date = ensure_utc(end_utc).date()
        rows = repo.fetch_daily_range(db, start_day, end_day, scope)
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "date": r.date.isoformat(),
                    "exchange": r.exchange,
                    "account_id": r.account_id,
                    "symbol": r.symbol,
                    "realized_usd": float(r.realized_usd or 0),
                    "fees_usd": float(r.fees_usd or 0),
                }
            )
        return out
