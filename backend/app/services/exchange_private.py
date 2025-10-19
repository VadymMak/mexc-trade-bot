# app/services/exchange_private.py
from __future__ import annotations

import enum
import hashlib
import hmac
import time
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple
from datetime import datetime

import aiohttp  # async HTTP

from app.config.settings import settings


# ─────────────────────────── Data Models ───────────────────────────

@dataclass
class BalanceInfo:
    asset: str
    free: float
    locked: float = 0.0
    total: Optional[float] = None  # free + locked if known


@dataclass
class PositionInfo:
    symbol: str          # normalized like "ETHUSDT"
    qty: float           # >0 long, <0 short (spot often ≥0 only)
    avg_price: float     # average entry (or 0 if exchange doesn’t track)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    ts_ms: int = 0       # exchange/server time if available


@dataclass
class OrderRequest:
    symbol: str                 # normalized like "ETHUSDT"
    side: str                   # "BUY" | "SELL"
    qty: float
    price: Optional[float] = None   # None => market
    type: str = "MARKET"            # "MARKET" | "LIMIT"
    tif: Optional[str] = None       # e.g. "IOC", "GTC"
    tag: Optional[str] = None       # strategy tag / client note


@dataclass
class OrderResult:
    ok: bool
    client_order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    status: Optional[str] = None            # "FILLED" | "NEW" | ...
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    executed_at: Optional[datetime] = None  # exchange/server time if known
    fee: Optional[float] = None
    fee_asset: Optional[str] = None
    trade_id: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


# ─────────────────────────── Provider Enum ─────────────────────────

class Provider(enum.Enum):
    MEXC = "MEXC"
    BINANCE = "BINANCE"
    GATE   = "GATE"

    @staticmethod
    def from_settings() -> "Provider":
        prov = (settings.active_provider or "MEXC").upper()
        if prov in {"GATE", "GATEIO", "GATE.IO"}:
            return Provider.GATE
        if prov == "BINANCE":
            return Provider.BINANCE
        return Provider.MEXC


# ─────────────────────── Unified Private API (Protocol) ───────────────────────

class ExchangePrivate(Protocol):
    def normalize_symbol(self, symbol: str) -> str: ...
    def provider_symbol(self, symbol: str) -> str: ...

    async def fetch_balances(self) -> List[BalanceInfo]: ...
    async def fetch_positions(self) -> List[PositionInfo]: ...

    async def place_order(self, req: OrderRequest) -> OrderResult: ...
    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool: ...
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: ...

    async def close_all_positions(self, use_market: bool = True) -> Dict[str, Any]: ...

    async def aclose(self) -> None: ...
    async def __aenter__(self): ...  # pragma: no cover
    async def __aexit__(self, exc_type, exc, tb): ...  # pragma: no cover


# ───────────────────────── Mock Implementation ─────────────────────────

class MockExchangePrivate(ExchangePrivate):
    """Mock for DEMO/PAPER w/o keys — returns fake data, no real API calls."""

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace(" ", "")

    def provider_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    async def fetch_balances(self) -> List[BalanceInfo]:
        return [
            BalanceInfo(asset="USDT", free=10000.0, locked=0.0, total=10000.0),
            BalanceInfo(asset="BTC", free=0.5, locked=0.0, total=0.5),
            BalanceInfo(asset="ETH", free=10.0, locked=0.0, total=10.0),
        ]

    async def fetch_positions(self) -> List[PositionInfo]:
        now = int(time.time() * 1000)
        return [
            PositionInfo(symbol="BTCUSDT", qty=0.5, avg_price=50000.0, ts_ms=now),
            PositionInfo(symbol="ETHUSDT", qty=10.0, avg_price=3000.0, ts_ms=now),
        ]

    async def place_order(self, req: OrderRequest) -> OrderResult:
        return OrderResult(
            ok=True,
            client_order_id=f"mock_{int(time.time())}",
            exchange_order_id=f"ex_mock_{int(time.time())}",
            status="FILLED",
            filled_qty=float(req.qty),
            avg_fill_price=float(req.price or 0.0),
            executed_at=datetime.utcnow(),
            raw={"mock": True, "req": req.__dict__},
        )

    async def cancel_order(self, symbol: str, client_order_id: Optional[str] = None,
                           exchange_order_id: Optional[str] = None) -> bool:
        return True

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    async def close_all_positions(self, use_market: bool = True) -> Dict[str, Any]:
        return {"closed": ["BTCUSDT", "ETHUSDT"], "errors": []}

    async def aclose(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


# ───────────────────────── MEXC Implementation ─────────────────────────

class MexcPrivate(ExchangePrivate):
    """MEXC Spot Private API Client (async)."""

    BASE_URL = "https://api.mexc.com"
    TESTNET_URL = "https://api.mexcdevelop.com"

    def __init__(self, sandbox: bool = False):
        # Prefer Settings; env is a fallback
        self.api_key = settings.api_key or os.getenv("MEXC_API_KEY")
        self.secret = settings.api_secret or os.getenv("MEXC_SECRET")
        if not self.api_key or not self.secret:
            # LIVE must have keys; for PAPER/DEMO the factory avoids constructing us
            raise ValueError("MEXC_API_KEY and MEXC_SECRET must be set in environment")
        self.base_url = self.TESTNET_URL if sandbox else self.BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None

    def _sign_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sign MEXC request with HMAC-SHA256 (Spot v3)."""
        # MEXC expects timestamp included in signature
        params = dict(params or {})
        timestamp = str(int(time.time() * 1000))
        params["timestamp"] = timestamp
        # Sort keys and build canonical query
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
        signature = hmac.new(
            self.secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def aclose(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace(" ", "")

    def provider_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = await self._ensure_session()
        signed = self._sign_request(params or {})
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MEXC-APIKEY": self.api_key}
        async with session.request(method.upper(), url, params=signed, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise Exception(f"MEXC API error {resp.status}: {body}")
            return await resp.json()

    async def fetch_balances(self) -> List[BalanceInfo]:
        data = await self._request("GET", "/api/v3/account", {})
        balances: List[BalanceInfo] = []
        for asset_data in data.get("balances", []):
            asset = asset_data.get("asset")
            free = float(asset_data.get("free", 0) or 0)
            locked = float(asset_data.get("locked", 0) or 0)
            balances.append(BalanceInfo(asset=asset, free=free, locked=locked, total=free + locked))
        return balances

    async def fetch_positions(self) -> List[PositionInfo]:
        # Spot: derive “positions” from non-quote balances
        balances = await self.fetch_balances()
        ts_ms = int(time.time() * 1000)
        res: List[PositionInfo] = []
        for bal in balances:
            if bal.asset and bal.asset.upper() != "USDT" and bal.free > 0:
                res.append(PositionInfo(
                    symbol=f"{bal.asset.upper()}USDT",
                    qty=bal.free,
                    avg_price=0.0,  # historical avg not available from this endpoint
                    ts_ms=ts_ms,
                ))
        return res

    async def place_order(self, req: OrderRequest) -> OrderResult:
        params: Dict[str, Any] = {
            "symbol": self.provider_symbol(req.symbol),
            "side": req.side.upper(),
            "type": req.type.upper(),
            "quantity": str(req.qty),
        }
        if req.price is not None:
            params["price"] = str(req.price)
        if req.tif:
            params["timeInForce"] = req.tif
        if req.tag:
            params["newClientOrderId"] = req.tag

        data = await self._request("POST", "/api/v3/order", params)
        executed_qty = float(data.get("executedQty", 0) or 0)
        cum_quote = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg_price = (cum_quote / executed_qty) if executed_qty > 0 else 0.0

        return OrderResult(
            ok=data.get("orderId") is not None,
            client_order_id=data.get("clientOrderId") or req.tag,
            exchange_order_id=str(data.get("orderId")) if data.get("orderId") is not None else None,
            status=data.get("status"),
            filled_qty=executed_qty,
            avg_fill_price=avg_price,
            executed_at=None,  # MEXC order response doesn't always include server-time for fills
            raw=data,
        )

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool:
        params: Dict[str, Any] = {"symbol": self.provider_symbol(symbol)}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        elif exchange_order_id:
            params["orderId"] = exchange_order_id
        else:
            return False
        data = await self._request("DELETE", "/api/v3/order", params)
        return bool(data.get("orderId") or data.get("clientOrderId"))

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = self.provider_symbol(symbol)
        data = await self._request("GET", "/api/v3/openOrders", params)
        # MEXC returns a list
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []  # defensive


# ───────────────────────── Factory ─────────────────────────

def _have_mexc_keys() -> bool:
    k = settings.api_key or os.getenv("MEXC_API_KEY")
    s = settings.api_secret or os.getenv("MEXC_SECRET")
    return bool(k and s)

# app/services/exchange_private.py  — заменить только эту функцию
def get_private_client(provider: Optional[str] = None, sandbox: bool = False, mock: bool = False) -> ExchangePrivate:
    """
    Возвращает приватный клиент по активному провайдеру/режиму.
    Правила:
      - DEMO: всегда мок.
      - PAPER: если нет ключей → мок; если ключи есть → реальный клиент.
      - LIVE: реальный клиент, ошибки пробрасываем.
      - Любая ошибка создания реального клиента в DEMO/PAPER → фоллбэк на мок.
    """
    mode = (settings.active_mode or "PAPER").upper()

    # DEMO всегда мок
    if mode == "DEMO" or mock:
        return MockExchangePrivate()

    # Определяем провайдера
    prov = Provider(provider.upper()) if provider else Provider.from_settings()

    # Хелпер проверки наличия ключей по провайдеру
    def _has_keys() -> bool:
        if prov == Provider.MEXC:
            return bool(os.getenv("MEXC_API_KEY") and os.getenv("MEXC_SECRET"))
        if prov == Provider.GATE:
            # можно учесть GATE_WS_ENV, но для простоты проверим обе пары
            return bool(os.getenv("GATE_API_KEY") and os.getenv("GATE_API_SECRET")) or \
                   bool(os.getenv("GATE_TESTNET_API_KEY") and os.getenv("GATE_TESTNET_API_SECRET"))
        if prov == Provider.BINANCE:
            return bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET"))
        return False

    # PAPER: без ключей — мок
    if mode == "PAPER" and not _has_keys():
        return MockExchangePrivate()

    # Пробуем реальный клиент
    try:
        if prov == Provider.GATE:
            from app.services.gate_private import GatePrivate  # type: ignore
            return GatePrivate(sandbox=(mode == "DEMO"))
        if prov == Provider.BINANCE:
            # Пока не реализовано — в PAPER откатываемся на мок, в LIVE кидаем явную ошибку
            if mode == "LIVE":
                raise NotImplementedError("Binance private client not implemented yet.")
            return MockExchangePrivate()
        # default: MEXC
        return MexcPrivate(sandbox=(mode == "DEMO"))
    except Exception as e:
        # В DEMO/PAPER — фоллбэк на мок, в LIVE — пробрасываем
        if mode in {"DEMO", "PAPER"}:
            print(f"[Config] Private client fallback to MOCK ({prov.value}) due to: {e}")
            return MockExchangePrivate()
        raise

