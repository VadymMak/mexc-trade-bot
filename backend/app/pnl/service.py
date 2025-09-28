# app/pnl/service.py
from __future__ import annotations

from dataclasses import asdict
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
    PnLComponents,
    PortfolioAsset,
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


# ─────────────────────────────── Service API ───────────────────────────────

class PnlService:
    """
    High-level PnL service:
    - Emits realized-affecting events to the ledger
    - Fetches summaries/symbol details/daily history for UI
    - Computes period windows (today/WTD/MTD/custom)
    - Emits SSE 'pnl_tick' after successful ledger writes
    """

    # If you add provider-side normalization later, wire it here
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
        """
        Normalization policy:
        - If amount_usd provided → trust it.
        - Else, if price_usd provided → amount_usd = amount_asset * price_usd
        - Else, if quote_asset is USDT/USDC/FDUSD/BUSD → treat amount_asset as USD-terms
        - Else → raise ValueError (caller must supply amount_usd or price_usd)
        """
        if amount_usd is not None:
            return amount_asset, amount_usd

        if price_usd is not None:
            return amount_asset, (amount_asset * price_usd)

        # Simple stable-coin heuristic; extend if needed
        if quote_asset.upper() in {"USDT", "USDC", "FDUSD", "BUSD"}:
            return amount_asset, amount_asset

        raise ValueError("normalize_event requires amount_usd or price_usd when quote_asset is not a stablecoin")

    # ── Write operations ──────────────────────────────────────────────────

    def log_trade_realized(
        self,
        db: Session,
        *,
        ts: datetime,
        exchange: str,
        account_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        realized_asset: Decimal,
        realized_usd: Optional[Decimal] = None,
        price_usd: Optional[Decimal] = None,
        ref_order_id: Optional[str] = None,
        ref_trade_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        emit_sse: bool = True,
    ) -> None:
        """Record a TRADE_REALIZED event (positive or negative)."""
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
            _emit_pnl_tick(
                {
                    "exchange": exchange,
                    "account_id": account_id,
                    "symbol": symbol,
                    "ts": _isoz(ts),
                    "delta_usd": float(amt_usd),
                    "event_type": "TRADE_REALIZED",
                }
            )

    def log_fee(
        self,
        db: Session,
        *,
        ts: datetime,
        exchange: str,
        account_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        fee_asset_delta: Decimal,  # pass negative for a fee (e.g., -0.001 USDT)
        fee_usd: Optional[Decimal] = None,
        price_usd: Optional[Decimal] = None,
        ref_order_id: Optional[str] = None,
        ref_trade_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        emit_sse: bool = True,
    ) -> None:
        """Record a FEE event (usually negative)."""
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
            _emit_pnl_tick(
                {
                    "exchange": exchange,
                    "account_id": account_id,
                    "symbol": symbol,
                    "ts": _isoz(ts),
                    "delta_usd": float(amt_usd),
                    "event_type": "FEE",
                }
            )

    def log_funding(
        self,
        db: Session,
        *,
        ts: datetime,
        exchange: str,
        account_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        funding_asset_delta: Decimal,  # positive for rebate, negative for payment
        funding_usd: Optional[Decimal] = None,
        price_usd: Optional[Decimal] = None,
        ref_order_id: Optional[str] = None,
        ref_trade_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        emit_sse: bool = True,
    ) -> None:
        """Record a FUNDING event (perps; positive=rebate, negative=payment)."""
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
            ref_order_id=ref_order_id,
            ref_trade_id=ref_trade_id,
            meta=meta or {},
        )
        repo.insert_ledger_event(db, event, dedupe=True)

        if emit_sse:
            _emit_pnl_tick(
                {
                    "exchange": exchange,
                    "account_id": account_id,
                    "symbol": symbol,
                    "ts": _isoz(ts),
                    "delta_usd": float(amt_usd),
                    "event_type": "FUNDING",
                }
            )

    def log_conversion_pnl(
        self,
        db: Session,
        *,
        ts: datetime,
        exchange: str,
        account_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        conversion_asset_delta: Decimal,   # +/-
        conversion_usd: Optional[Decimal] = None,
        price_usd: Optional[Decimal] = None,
        ref_order_id: Optional[str] = None,
        ref_trade_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        emit_sse: bool = True,
    ) -> None:
        """Record a CONVERSION_PNL event (revaluation when converting balances)."""
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
            _emit_pnl_tick(
                {
                    "exchange": exchange,
                    "account_id": account_id,
                    "symbol": symbol,
                    "ts": _isoz(ts),
                    "delta_usd": float(amt_usd),
                    "event_type": "CONVERSION_PNL",
                }
            )

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
        """Windowed total + breakdowns by exchange & symbol."""
        start_utc, end_utc = period_window(period, tz=tz, now=now)
        total_usd, by_exchange, by_symbol = repo.aggregate_summary(db, start_utc, end_utc, scope)
        return PnlSummary(
            period=period,
            total_usd=total_usd,
            by_exchange=by_exchange,
            by_symbol=by_symbol,
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
    ) -> PnlSymbolDetail:
        """Per-symbol totals + component breakdown + recent events."""
        start_utc, end_utc = period_window(period, tz=tz, now=now)

        scope: repo.Scope = {"symbol": symbol}
        if exchange:
            scope["exchange"] = exchange
        if account_id:
            scope["account_id"] = account_id

        parts = repo.aggregate_symbol_components(db, start_utc, end_utc, scope)
        last_events = repo.fetch_last_events(db, start_utc, end_utc, scope, limit=events_limit)
        total_usd = float(parts["trade_realized"] + parts["fees"] + parts["funding"] + parts["conversion"])

        return PnlSymbolDetail(
            symbol=symbol,
            exchange=exchange or "",
            account_id=account_id or "",
            total_usd=total_usd,
            components=PnLComponents(
                trade_realized=float(parts["trade_realized"]),
                fees=float(parts["fees"]),
                funding=float(parts["funding"]),
                conversion=float(parts["conversion"]),
            ),
            last_events=last_events,
        )

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
