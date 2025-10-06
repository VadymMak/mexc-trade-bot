# app/execution/live_executor.py
from __future__ import annotations

from typing import Any, Dict, Optional, Literal, Tuple, Protocol, List
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.pnl.service import PnlService
from app.services.exchange_private import (
    get_private_client,
    OrderRequest,
    OrderResult,
    ExchangePrivate,
)

# Для mark-price в get_position
from app.services import book_tracker as bt_service
from app.services.book_tracker import ensure_symbols_subscribed

# МОДЕЛИ БД (исправление ошибки "Position is not defined")
from app.models.positions import Position, PositionSide, PositionStatus

Side = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]
TimeInForce = Literal["GTC", "IOC", "FOK"]


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


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


def _is_usd_quote(quote: str) -> bool:
    return quote in {"USDT", "USDC", "FDUSD", "BUSD"}


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LiveExecutor:
    """
    Live-исполнитель поверх унифицированного ExchangePrivate клиента.

    - Реализует интерфейс PaperExecutor для StrategyEngine:
      start_symbol / stop_symbol / flatten_symbol / cancel_orders / place_maker / get_position
    - place_maker: по умолчанию MARKET (надёжное исполнение).
      Можно отключить через settings.live_use_market_for_maker = False — тогда LIMIT по заданной цене.
    - Записывает TRADE_REALIZED и FEE в PnL-леджер (если передан session_factory).
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

        # Поведение «maker»: по умолчанию — MARKET ради гарантированного исполнения.
        try:
            self._use_market_for_maker: bool = bool(
                getattr(settings, "live_use_market_for_maker", True)
            )
        except Exception:
            self._use_market_for_maker = True

    # ------------------- lifecycle -------------------

    async def aclose(self) -> None:
        try:
            await self._client.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------- StrategyEngine-compatible ops -------------------

    async def start_symbol(self, symbol: str) -> None:
        """Подписка на котировки — best-effort (полезно для mark-price)."""
        try:
            await ensure_symbols_subscribed([symbol.upper()])
        except Exception:
            pass

    async def stop_symbol(self, symbol: str) -> None:
        """Для spot обычно NOP (отмена ордеров делает cancel_orders)."""
        return None

    async def cancel_orders(self, symbol: str) -> None:
        """Отмена всех открытых ордеров по символу."""
        try:
            oo = await self._client.get_open_orders(symbol.upper())
            if isinstance(oo, list):
                for o in oo:
                    try:
                        await self._client.cancel_order(
                            symbol=symbol.upper(),
                            client_order_id=str(
                                o.get("client_order_id") or o.get("clientOid") or ""
                            ),
                            exchange_order_id=str(
                                o.get("order_id") or o.get("orderId") or ""
                            ),
                        )
                    except Exception:
                        continue
        except Exception:
            pass

    async def flatten_symbol(self, symbol: str) -> None:
        """Закрыть текущую лонг-позицию MARKET SELL (надёжно)."""
        sym = symbol.upper()
        try:
            pos = await self._find_live_position(sym)
            qty = _dec(pos.get("qty")) if pos else Decimal("0")
            if qty <= 0:
                return
            req = OrderRequest(
                symbol=sym,
                side="SELL",
                qty=float(qty),
                type="MARKET",
                tif="IOC",
                price=None,
                tag="flatten",
            )
            result: OrderResult = await self._client.place_order(req)
            await self._try_log_pnl(sym, "SELL", result)
        except Exception:
            # Никогда не роняем стратегию на попытке flatten
            pass

    async def place_maker(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        tag: str = "mm",
    ) -> Optional[str]:
        """
        Live «maker»-операция для StrategyEngine.

        По умолчанию — MARKET (надёжное исполнение).
        Если settings.live_use_market_for_maker=False, шлём LIMIT по заданной цене (GTC).
        Возвращает client_order_id при успешном размещении.
        """
        sym = symbol.upper()
        s_up = side.upper().strip()
        qf = float(qty)
        if qf <= 0:
            return None

        try:
            await ensure_symbols_subscribed([sym])
        except Exception:
            pass

        if self._use_market_for_maker:
            req = OrderRequest(
                symbol=sym,
                side=s_up,  # BUY/SELL
                qty=qf,
                type="MARKET",
                tif="IOC",
                price=None,
                tag=tag,
            )
        else:
            # Осторожный LIMIT: без postOnly (зависит от провайдера), GTC.
            req = OrderRequest(
                symbol=sym,
                side=s_up,
                qty=qf,
                type="LIMIT",
                tif="GTC",
                price=float(price) if price is not None else None,
                tag=tag,
            )

        try:
            result: OrderResult = await self._client.place_order(req)
            # PnL: если сразу исполнилось — залогируем
            try:
                await self._try_log_pnl(sym, s_up, result)
            except Exception:
                pass
            return str(result.client_order_id or "") if getattr(result, "ok", False) else None
        except Exception:
            return None

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        """
        Нормализованный снимок позиции для UI:
        {symbol, qty, avg_price, unrealized_pnl, realized_pnl, ts_ms}
        """
        sym = symbol.upper()
        try:
            pos = await self._find_live_position(sym)
        except Exception:
            pos = None

        qty = _dec(pos.get("qty")) if pos else Decimal("0")
        avg = _dec(pos.get("avg_price")) if pos else Decimal("0")
        realized = _dec(pos.get("realized_pnl")) if pos else Decimal("0")

        # mark-price (mid) для upnl
        try:
            q = await bt_service.get_quote(sym)
            bid = _dec(q.get("bid", 0.0))
            ask = _dec(q.get("ask", 0.0))
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
        except Exception:
            mid = Decimal("0")

        upnl = (mid - avg) * qty if (qty > 0 and mid > 0) else Decimal("0")
        return {
            "symbol": sym,
            "qty": float(qty),
            "avg_price": float(avg),
            "unrealized_pnl": float(upnl),
            "realized_pnl": float(realized),
            "ts_ms": int(datetime.utcnow().timestamp() * 1000),
        }

    # ------------------- internal helpers -------------------

    async def _find_live_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Унифицированный способ получить текущий спот-«лонг»:
        - сперва пробуем fetch_positions()
        - если там пусто, пробуем собрать из балансов (qty по базовому активу)
        """
        sym = symbol.upper()
        base, _quote = _split_symbol(sym)

        # 1) Нативные позиции (если провайдер их возвращает)
        try:
            poss = await self._client.fetch_positions()
            # Ожидаем поля: symbol, qty, avg_price, realized_pnl
            for p in poss or []:
                ps = getattr(p, "symbol", None) or getattr(p, "ticker", None) or str(getattr(p, "s", sym))
                if str(ps).upper() == sym:
                    return {
                        "symbol": sym,
                        "qty": getattr(p, "qty", 0.0),
                        "avg_price": getattr(p, "avg_price", 0.0),
                        "realized_pnl": getattr(p, "realized_pnl", 0.0),
                    }
        except Exception:
            pass

        # 2) Фоллбек от балансов: qty = free+locked базового актива
        try:
            bals = await self._client.fetch_balances()
            total_base = Decimal("0")
            for b in bals or []:
                asset = str(getattr(b, "asset", "") or getattr(b, "currency", "") or "").upper()
                if asset == base:
                    free_ = _dec(getattr(b, "free", 0))
                    locked_ = _dec(getattr(b, "locked", 0))
                    total_base += (free_ + locked_)
            if total_base > 0:
                return {"symbol": sym, "qty": float(total_base), "avg_price": 0.0, "realized_pnl": 0.0}
        except Exception:
            pass

        return None

    # ------------------- generic trading API -------------------

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
        reduce_only: Optional[bool] = None,  # для совместимости; на spot игнорируется
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Прямое размещение ордера (используется роутерами /api/exec/place)."""
        req = OrderRequest(
            symbol=symbol.upper(),
            side=side,
            qty=float(quantity),
            price=float(price) if (price is not None) else None,
            type=type,
            tif=time_in_force,
            tag=tag or client_order_id,
        )
        result: OrderResult = await self._client.place_order(req)

        # PnL (best-effort)
        try:
            await self._try_log_pnl(symbol, side, result)
        except Exception:
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
        ok = await self._client.cancel_order(
            symbol=symbol.upper(),
            client_order_id=orig_client_order_id,
            exchange_order_id=order_id,
        )
        return {"ok": ok}

    async def get_open_orders(self, *, symbol: Optional[str] = None) -> Dict[str, Any]:
        data = await self._client.get_open_orders(symbol.upper() if symbol else None)
        return {"data": data}

    async def get_account_info(self) -> Dict[str, Any]:
        bals = await self._client.fetch_balances()
        poss = await self._client.fetch_positions()
        return {
            "balances": [b.__dict__ for b in bals],
            "positions": [p.__dict__ for p in poss],
        }

    async def close_all_positions(self, *, use_market: bool = True) -> Dict[str, Any]:
        return await self._client.close_all_positions(use_market=use_market)

    # ------------------- PnL helpers -------------------

    async def _try_log_pnl(self, symbol: str, side: Side, result: Any) -> None:
        """
        Если есть DB-сессия и была сделка — пишем TRADE_REALIZED и FEE.
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

        # executed_at → naive UTC
        executed_at = getattr(result, "executed_at", None)
        if isinstance(executed_at, datetime):
            executed_at = executed_at.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            executed_at = _now_utc_naive()

        session: Session = self._session_factory()
        try:
            # Только SELL против открытого лонга
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

                        meta = {
                            "meta_ver": 1,
                            "mode": "live",
                            "side": "SELL",
                            "qty": float(close_qty),
                            "price": float(avg_fill_price),
                            "fee": float(getattr(result, "fee", 0.0) or 0.0),
                            "fee_asset": str(getattr(result, "fee_asset", "USDT") or "USDT"),
                            "client_order_id": getattr(result, "client_order_id", None),
                            "exchange_order_id": getattr(result, "exchange_order_id", None),
                            "trade_id": str(getattr(result, "trade_id", "") or ""),
                            "strategy_tag": getattr(result, "tag", None),
                        }

                        self._pnl.log_trade_realized(
                            session,
                            ts=executed_at,
                            exchange=str(ex),
                            account_id=str(acc),
                            symbol=symbol.upper(),
                            base_asset=base,
                            quote_asset=quote,
                            realized_asset=pnl_usd,
                            realized_usd=pnl_usd,
                            price_usd=(Decimal("1") if _is_usd_quote(quote) else None),
                            ref_order_id=str(
                                getattr(result, "exchange_order_id", "")
                                or getattr(result, "client_order_id", "")
                            ),
                            ref_trade_id=str(getattr(result, "trade_id", "") or ""),
                            meta=meta,
                            emit_sse=True,
                        )

            # Комиссия (best-effort)
            raw = getattr(result, "raw", None) or {}
            self._try_log_fee_from_raw(session, symbol, raw, result, executed_at)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _try_log_fee_from_raw(
        self,
        session: Session,
        symbol: str,
        raw: Dict[str, Any],
        result: Any,
        executed_at: datetime,
    ) -> None:
        """
        Частые формы комиссий:
          - {"fee": 0.001, "feeAsset": "USDT"}
          - {"fees": [{"asset":"USDT","amount":0.001}, ...]}
          - {"fills":[{"commission":"0.001","commissionAsset":"USDT"}, ...]}  # Binance-style
        """
        if not raw:
            return

        entries: List[Tuple[Decimal, str]] = []

        if "fee" in raw:
            try:
                amt = _dec(raw.get("fee"))
                asset = str(raw.get("feeAsset") or raw.get("fee_asset") or "USDT").upper()
                if amt:
                    entries.append((amt, asset))
            except Exception:
                pass

        fees_arr = raw.get("fees")
        if isinstance(fees_arr, list):
            for f in fees_arr:
                try:
                    amt = _dec(f.get("amount"))
                    asset = str(f.get("asset") or "USDT").upper()
                    if amt:
                        entries.append((amt, asset))
                except Exception:
                    continue

        fills_arr = raw.get("fills")
        if isinstance(fills_arr, list):
            for f in fills_arr:
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
            fee_usd = -amt if _is_usd_quote(asset) else None  # stable = 1:1
            self._pnl.log_fee(
                session,
                ts=executed_at,
                exchange=str(ex),
                account_id=str(acc),
                symbol=symbol.upper(),
                base_asset=base,
                quote_asset=quote,
                fee_asset=asset,
                fee_asset_delta=-amt,   # отрицательное
                fee_usd=fee_usd,
                price_usd=None,        # можно прокинуть конвертацию позже
                ref_order_id=str(
                    getattr(result, "exchange_order_id", "")
                    or getattr(result, "client_order_id", "")
                ),
                ref_trade_id=str(getattr(result, "trade_id", "") or ""),
                meta={
                    "meta_ver": 1,
                    "mode": "live",
                    "fee": float(amt),
                    "fee_asset": asset,
                    "client_order_id": getattr(result, "client_order_id", None),
                    "exchange_order_id": getattr(result, "exchange_order_id", None),
                    "trade_id": str(getattr(result, "trade_id", "") or ""),
                    "strategy_tag": getattr(result, "tag", None),
                },
                emit_sse=True,
            )
