# app/services/exchange_private.py
from __future__ import annotations

import enum
import hashlib
import hmac
import time
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple
from datetime import datetime  # NEW

import aiohttp  # Assume installed; for async HTTP

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
    # NEW: values used by LiveExecutor for PnL/fees
    executed_at: Optional[datetime] = None  # exchange/server time of execution if known
    fee: Optional[float] = None             # total fee for this order/fill if provider reports it
    fee_asset: Optional[str] = None         # e.g. "USDT"
    trade_id: Optional[str] = None          # provider trade id (if available)
    raw: Optional[Dict[str, Any]] = None    # full provider payload (for debugging)


# ─────────────────────────── Provider Enum ─────────────────────────

class Provider(enum.Enum):
    MEXC = "MEXC"
    BINANCE = "BINANCE"
    GATE   = "GATE"

    @staticmethod
    def from_settings() -> "Provider":
        # prefer the resolved/normalized provider
        prov = (settings.active_provider or "MEXC").upper()
        if prov in {"GATE", "GATEIO", "GATE.IO"}:
            return Provider.GATE
        if prov == "BINANCE":
            return Provider.BINANCE
        return Provider.MEXC


# ─────────────────────── Unified Private API (Protocol) ───────────────────────

class ExchangePrivate(Protocol):
    """
    Minimal cross-exchange private interface for:
      - balances
      - positions
      - orders
      - convenience ops (close-all)
    Implementations must also provide an async close() hook.
    """
    # --- symbol mapping helpers (provider-specific normalization) ---
    def normalize_symbol(self, symbol: str) -> str: ...
    def provider_symbol(self, symbol: str) -> str: ...

    # --- account state ---
    async def fetch_balances(self) -> List[BalanceInfo]: ...
    async def fetch_positions(self) -> List[PositionInfo]: ...

    # --- orders ---
    async def place_order(self, req: OrderRequest) -> OrderResult: ...
    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool: ...
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: ...

    # --- convenience trade op ---
    async def close_all_positions(self, use_market: bool = True) -> Dict[str, Any]: ...

    # --- lifecycle ---
    async def aclose(self) -> None: ...
    # (optional) async context manager support
    async def __aenter__(self): ...  # pragma: no cover
    async def __aexit__(self, exc_type, exc, tb): ...  # pragma: no cover


# ───────────────────────── Mock Implementation ─────────────────────────

class MockExchangePrivate(ExchangePrivate):
    """Mock for DEMO mode - returns fake data, no real API calls."""

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
        return [
            PositionInfo(symbol="BTCUSDT", qty=0.5, avg_price=50000.0, ts_ms=int(time.time() * 1000)),
            PositionInfo(symbol="ETHUSDT", qty=10.0, avg_price=3000.0, ts_ms=int(time.time() * 1000)),
        ]

    async def place_order(self, req: OrderRequest) -> OrderResult:
        return OrderResult(
            ok=True,
            client_order_id=f"mock_{int(time.time())}",
            exchange_order_id=f"ex_mock_{int(time.time())}",
            status="FILLED",
            filled_qty=req.qty,
            avg_fill_price=req.price or 0.0,
            executed_at=datetime.utcnow(),
            raw={"mock": True},
        )

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool:
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
    """MEXC Spot Private API Client (async). Assumes spot trading."""

    BASE_URL = "https://api.mexc.com"
    TESTNET_URL = "https://api.mexcdevelop.com"

    def __init__(self, sandbox: bool = False):
        self.api_key = os.getenv("MEXC_API_KEY")
        self.secret = os.getenv("MEXC_SECRET")
        if not self.api_key or not self.secret:
            raise ValueError("MEXC_API_KEY and MEXC_SECRET must be set in environment")
        self.base_url = self.TESTNET_URL if sandbox else self.BASE_URL
        self.session = None

    def _sign_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sign MEXC request with HMAC-SHA256."""
        timestamp = str(int(time.time() * 1000))
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items()) if v is not None])
        signature = hmac.new(
            self.secret.encode("utf-8"),
            f"{query_string}&timestamp={timestamp}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["timestamp"] = timestamp
        params["signature"] = signature
        return params

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def aclose(self) -> None:
        if self.session:
            await self.session.close()

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace(" ", "")  # e.g., "BTC USDT" -> "BTCUSDT"

    def provider_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)  # MEXC uses same format

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Private signed request helper."""
        if params is None:
            params = {}
        params = self._sign_request(params)
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MEXC-APIKEY": self.api_key}
        async with self.session.request(method, url, params=params, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"MEXC API error {resp.status}: {await resp.text()}")
            return await resp.json()

    async def fetch_balances(self) -> List[BalanceInfo]:
        data = await self._request("GET", "/api/v3/account")
        balances = []
        for asset_data in data.get("balances", []):
            asset = asset_data["asset"]
            free = float(asset_data["free"])
            locked = float(asset_data["locked"])
            total = free + locked
            balances.append(BalanceInfo(asset=asset, free=free, locked=locked, total=total))
        return balances

    async def fetch_positions(self) -> List[PositionInfo]:
        # For spot, positions derived from balances (qty > 0 for base assets)
        balances = await self.fetch_balances()
        positions = []
        ts_ms = int(time.time() * 1000)
        # Simplified: assume USDT pairs, fetch ticker for avg_price approx (but set to 0 for now)
        for bal in balances:
            if bal.asset != "USDT" and bal.free > 0:  # Base asset with balance
                symbol = f"{bal.asset}USDT"
                positions.append(PositionInfo(
                    symbol=symbol,
                    qty=bal.free,
                    avg_price=0.0,  # Would need historical avg; stub for now
                    ts_ms=ts_ms
                ))
        return positions

    async def place_order(self, req: OrderRequest) -> OrderResult:
        params = {
            "symbol": self.provider_symbol(req.symbol),
            "side": req.side,
            "type": req.type,
            "quantity": str(req.qty),
        }
        if req.price:
            params["price"] = str(req.price)
        if req.tif:
            params["timeInForce"] = req.tif
        if req.tag:
            params["newClientOrderId"] = req.tag

        data = await self._request("POST", "/api/v3/order", params)
        return OrderResult(
            ok=data.get("orderId") is not None,
            client_order_id=req.tag,
            exchange_order_id=data.get("orderId"),
            status=data.get("status"),
            filled_qty=float(data.get("executedQty", 0)),
            avg_fill_price=float(data.get("cummulativeQuoteQty", 0)) / max(1, float(data.get("executedQty", 1))),
            raw=data,
        )

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool:
        params = {"symbol": self.provider_symbol(symbol)}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        elif exchange_order_id:
            params["orderId"] = exchange_order_id
        else:
            return False
        data = await self._request("DELETE", "/api/v3/order", params)
        return data.get("orderId") is not None

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = self.provider_symbol(symbol)
        data = await self._request("GET", "/api/v3/openOrders", params)
        return data

    async def close_all_positions(self, use_market: bool = True) -> Dict[str, Any]:
        positions = await self.fetch_positions()
        results = {"closed": [], "errors": []}
        for pos in positions:
            side = "SELL" if pos.qty > 0 else "BUY"
            req = OrderRequest(symbol=pos.symbol, side=side, qty=abs(pos.qty), type="MARKET" if use_market else "LIMIT")
            result = await self.place_order(req)
            if result.ok:
                results["closed"].append(pos.symbol)
            else:
                results["errors"].append(pos.symbol)
        return results


# ───────────────────────── Factory (providers added one-by-one) ─────────────────────────

def get_private_client(provider: Optional[str] = None, sandbox: bool = False, mock: bool = False) -> ExchangePrivate:
    """
    Returns an ExchangePrivate implementation based on settings.active_provider / active_mode.
    Supports MEXC, GATE (lazy), BINANCE (TBD). Uses provided args if given, else defaults.
    """
    # Force mock if DEMO mode, even if caller passes mock=False (fallback for reload issues)
    effective_mock = mock or (settings.active_mode == "DEMO")
    if effective_mock:
        return MockExchangePrivate()

    prov = Provider(provider.upper()) if provider else Provider.from_settings()
    if prov == Provider.GATE:
        # imported lazily to avoid hard dependency until the file exists
        from app.services.gate_private import GatePrivate  # type: ignore
        return GatePrivate()  # TODO: pass sandbox/mock if GatePrivate supports
    if prov == Provider.BINANCE:
        # to be implemented next (binance_private.py)
        raise NotImplementedError("Binance private client not implemented yet.")
    # default: MEXC
    return MexcPrivate(sandbox=sandbox)