# app/execution/live_executor.py
from __future__ import annotations

from typing import Any, Dict, Optional, Literal, Tuple
from datetime import datetime
from decimal import Decimal

from app.services.exchange_private import (
    get_private_client,
    OrderRequest,
    OrderResult,
    ExchangePrivate,
)

# Optional DB access for realized PnL calc
from sqlalchemy.orm import Session
from typing import Protocol

from app.models.positions import Position, PositionSide, PositionStatus
from app.config.settings import settings
from app.pnl.service import PnlService


Side = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]
TimeInForce = Literal["GTC", "IOC", "FOK"]


class SessionFactory(Protocol):
    def __call__(self) -> Session:
        ...


def _split_symbol(symbol: str) -> Tuple[str, str]:
    s = symbol.upper()
    for tail in ("USDT", "USDC", "FDUSD", "BUSD"):
        if s.endswith(tail):
            return s[: -len(tail)], tail
    return s[:-3] or s, s[-3:] or "USDT"


def _dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


class LiveExecutor:
    """
    Thin live executor that delegates to the unified ExchangePrivate client.
    Adds optional PnL logging (TRADE_REALIZED and FEE) when a DB session factory
    is provided so we can read current OPEN position to compute realized PnL.

    This keeps exchange-specific signing/quirks inside app/services/*_private.py.
    """

    def __init__(
        self,
        session_factory: Optional[SessionFactory] = None,
        workspace_id: int = 1,
    ) -> None:
        self._client: ExchangePrivate = get_private_client()
        self._pnl = PnlService()
        self._session_factory = session_factory
        self._wsid = workspace_id

    # ------------------- lifecycle -------------------

    async def aclose(self) -> None:
        try:
            await self._client.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------- trading ops -------------------

    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        time_in_force: TimeInForce = "GTC",
        client_order_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,  # kept for API compatibility; ignored on spot
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place an order through the active provider.
        Returns a normalized payload (dict) based on OrderResult.

        If a session_factory was provided and the order executes (filled_qty>0),
        we will:
         - On SELL against an OPEN long position: write TRADE_REALIZED to ledger.
         - If fee info is present in provider payload: write FEE to ledger.
        """
        req = OrderRequest(
            symbol=symbol.upper(),
            side=side,
            qty=float(quantity),
            price=float(price) if (price is not None) else None,
            type=type,
            tif=time_in_force,
            tag=tag or client_order_id,  # reuse user-supplied id as tag if present
        )
        result: OrderResult = await self._client.place_order(req)

        # Best-effort PnL writes (don't break trading if anything is missing)
        try:
            await self._try_log_pnl(symbol, side, result)
        except Exception:
            # never block trading on accounting
            pass

        return {
            "ok": result.ok,
            "status": result.status,
            "client_order_id": result.client_order_id,
            "exchange_order_id": result.exchange_order_id,
            "filled_qty": result.filled_qty,
            "avg_fill_price": result.avg_fill_price,
            "raw": result.raw,
        }

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: Optional[str] = None,
        orig_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cancel an order by exchange id or client id.
        """
        ok = await self._client.cancel_order(
            symbol=symbol.upper(),
            client_order_id=orig_client_order_id,
            exchange_order_id=order_id,
        )
        return {"ok": ok}

    async def get_open_orders(self, *, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch open orders; optionally filter by symbol.
        """
        data = await self._client.get_open_orders(symbol.upper() if symbol else None)
        return {"data": data}

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Minimal account snapshot: balances + derived spot positions.
        """
        bals = await self._client.fetch_balances()
        poss = await self._client.fetch_positions()
        return {
            "balances": [b.__dict__ for b in bals],
            "positions": [p.__dict__ for p in poss],
        }

    async def close_all_positions(self, *, use_market: bool = True) -> Dict[str, Any]:
        """
        Convenience: flatten all positions (spot: sell all non-quote balances).
        """
        return await self._client.close_all_positions(use_market=use_market)

    # ------------------- PnL helpers -------------------

    async def _try_log_pnl(self, symbol: str, side: Side, result: Any) -> None:
        """
        If we have a DB session and a fill occurred, write TRADE_REALIZED and FEE rows.
        """
        if not self._session_factory:
            return
        if not getattr(result, "filled_qty", None):
            return
        filled_qty = _dec(result.filled_qty)
        if filled_qty <= 0:
            return

        avg_fill_price = _dec(getattr(result, "avg_fill_price", 0))
        if avg_fill_price <= 0:
            return

        session: Session = self._session_factory()
        try:
            # Only handle SELL against an open long position (spot long-only assumption)
            if side == "SELL":
                pos: Optional[Position] = (
                    session.query(Position)
                    .filter(
                        Position.workspace_id == self._wsid,
                        Position.symbol == symbol.upper(),
                        Position.side == PositionSide.BUY,
                        Position.is_open == True,  # noqa: E712
                        Position.status == PositionStatus.OPEN,
                    )
                    .order_by(Position.id.desc())
                    .first()
                )
                if pos and _dec(pos.qty) > 0:
                    close_qty = min(filled_qty, _dec(pos.qty))
                    if close_qty > 0:
                        pnl_usd = (avg_fill_price - _dec(pos.entry_price)) * close_qty
                        base, quote = _split_symbol(symbol)
                        ex = getattr(settings, "active_provider", None) or "LIVE"
                        acc = getattr(settings, "account_id", None) or "spot"

                        # Write TRADE_REALIZED
                        self._pnl.log_trade_realized(
                            session,
                            ts=datetime.utcnow(),
                            exchange=str(ex),
                            account_id=str(acc),
                            symbol=symbol.upper(),
                            base_asset=base,
                            quote_asset=quote,
                            realized_asset=pnl_usd,  # quote-denominated delta is fine
                            realized_usd=pnl_usd,    # explicit USD(eq)
                            price_usd=None,
                            ref_order_id=str(getattr(result, "exchange_order_id", "") or getattr(result, "client_order_id", "")),
                            ref_trade_id=str(getattr(result, "trade_id", "") or ""),
                            meta={"mode": "live"},
                            emit_sse=True,
                        )

            # Try to log a FEE row if present in raw (best-effort; quietly ignore if absent)
            raw = getattr(result, "raw", None) or {}
            self._try_log_fee_from_raw(session, symbol, raw)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _try_log_fee_from_raw(self, session: Session, symbol: str, raw: Dict[str, Any]) -> None:
        """
        Try common fee shapes:
          - {"fee": 0.001, "feeAsset": "USDT"}
          - {"fees": [{"asset":"USDT","amount":0.001}, ...]}
          - {"fills":[{"commission":"0.001","commissionAsset":"USDT"}, ...]}  # Binance style
        Logs negative USD(eq) amounts; stablecoins treated 1:1 USD.
        """
        if not raw:
            return

        entries: list[Tuple[Decimal, str]] = []

        # shape 1
        if "fee" in raw:
            amt = _dec(raw.get("fee"))
            asset = str(raw.get("feeAsset") or raw.get("fee_asset") or "USDT").upper()
            if amt:
                entries.append((amt, asset))

        # shape 2
        if isinstance(raw.get("fees"), list):
            for f in raw["fees"]:
                try:
                    amt = _dec(f.get("amount"))
                    asset = str(f.get("asset") or "USDT").upper()
                    if amt:
                        entries.append((amt, asset))
                except Exception:
                    continue

        # shape 3 (binance fills)
        if isinstance(raw.get("fills"), list):
            for f in raw["fills"]:
                try:
                    amt = _dec(f.get("commission"))
                    asset = str(f.get("commissionAsset") or "USDT").upper()
                    if amt:
                        entries.append((amt, asset))
                except Exception:
                    continue

        if not entries:
            return

        base, quote = _split_symbol(symbol)
        ex = getattr(settings, "active_provider", None) or "LIVE"
        acc = getattr(settings, "account_id", None) or "spot"

        for amt, asset in entries:
            # fees are negative PnL
            fee_usd = -amt if asset in {"USDT", "USDC", "FDUSD", "BUSD"} else None
            self._pnl.log_fee(
                session,
                ts=datetime.utcnow(),
                exchange=str(ex),
                account_id=str(acc),
                symbol=symbol.upper(),
                base_asset=base,
                quote_asset=quote,
                fee_asset_delta=-amt,     # sign-aware (negative)
                fee_usd=fee_usd,          # if None, service will try to normalize (requires price_usd)
                price_usd=None,           # you can pass a conversion here later from your quotes
                ref_order_id=str(raw.get("orderId") or raw.get("order_id") or ""),
                ref_trade_id=str(raw.get("tradeId") or raw.get("trade_id") or ""),
                meta={"mode": "live"},
                emit_sse=True,
            )
