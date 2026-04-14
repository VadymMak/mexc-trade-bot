"""
researcher/app/live/gate_futures.py

Gate.io Futures (USDT-margined) API client.
Docs: https://www.gate.io/docs/developers/apiv4/#futures

Key differences from Gate Spot (gate_private.py):
  - Endpoints: /futures/usdt/  instead of /spot/
  - size field: positive = long contracts, negative = short contracts
  - price: "0" for market orders (tif="ioc")
  - Positions: GET /futures/usdt/positions/{contract}
  - Signing: identical HMAC-SHA512 — reuse same logic
  - Symbol:  BTC_USDT  (same underscore format as spot)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

from .base import FuturesClient, OrderResult, PositionInfo

logger = logging.getLogger(__name__)

_BASE_LIVE    = "https://api.gateio.ws/api/v4"
_BASE_TESTNET = "https://api-testnet.gateapi.io/api/v4"


def _hmac_sha512(secret: str, msg: str) -> str:
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha512).hexdigest()


class GateFutures:
    """
    Gate.io USDT-margined Futures private client.

    Usage:
        async with GateFutures(api_key, secret) as client:
            result = await client.open_short("BTC_USDT", usdt_size=10.0)
    """

    exchange_name = "gate"

    def __init__(self, api_key: str, secret: str, testnet: bool = False) -> None:
        self._key     = api_key
        self._secret  = secret
        self._base    = _BASE_TESTNET if testnet else _BASE_LIVE
        self._testnet = testnet
        self._session: Optional[aiohttp.ClientSession] = None
        if testnet:
            logger.warning("GateFutures running in TESTNET mode — virtual funds only")

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "GateFutures":
        self._session = aiohttp.ClientSession(
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Signing ────────────────────────────────────────────────────────────────

    def _signed_headers(
        self,
        method: str,
        path: str,
        query_str: str = "",
        body_str: str = "",
    ) -> Dict[str, str]:
        ts = str(int(time.time()))
        # Gate API v4 signature: METHOD\n/api/v4/PATH\nQUERY\nSHA512(body)\nTIMESTAMP
        # IMPORTANT: path must include /api/v4 prefix in signature
        full_path = f"/api/v4{path}"
        body_hash = hashlib.sha512(body_str.encode("utf-8")).hexdigest()
        msg = f"{method.upper()}\n{full_path}\n{query_str}\n{body_hash}\n{ts}"
        sign = _hmac_sha512(self._secret, msg)
        return {
            "KEY":          self._key,
            "Timestamp":    ts,
            "SIGN":         sign,
            "Content-Type": "application/json",
        }

    # ── HTTP ───────────────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        assert self._session
        query_str = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        headers = self._signed_headers("GET", path, query_str=query_str)
        url = f"{self._base}{path}"
        async with self._session.get(url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, body: Dict[str, Any]) -> Any:
        assert self._session
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._signed_headers("POST", path, body_str=body_str)
        url = f"{self._base}{path}"
        async with self._session.post(url, data=body_str, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── Symbol helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_contract(symbol: str) -> str:
        """BTCUSDT → BTC_USDT, BTC_USDT → BTC_USDT"""
        s = symbol.upper().replace("-", "_")
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s

    @staticmethod
    def _usdt_to_size(usdt_size: float) -> int:
        """
        Gate: 1 contract = 1 USDT notional (USDT-margined).
        size > 0 = long, size < 0 = short.
        """
        return max(1, int(usdt_size))

    # ── FuturesClient interface ────────────────────────────────────────────────

    async def open_long(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        return await self._place(symbol, size=+self._usdt_to_size(usdt_size), leverage=leverage)

    async def open_short(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        return await self._place(symbol, size=-self._usdt_to_size(usdt_size), leverage=leverage)

    async def close_long(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        vol = self._usdt_to_size(qty) if qty else await self._get_position_size(symbol, long=True)
        if vol <= 0:
            return OrderResult(ok=True, filled_qty=0, error="no position to close")
        return await self._place(symbol, size=-vol, leverage=1, reduce_only=True)

    async def close_short(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        vol = self._usdt_to_size(qty) if qty else await self._get_position_size(symbol, long=False)
        if vol <= 0:
            return OrderResult(ok=True, filled_qty=0, error="no position to close")
        return await self._place(symbol, size=+vol, leverage=1, reduce_only=True)

    async def get_position(self, symbol: str) -> PositionInfo:
        contract = self._to_contract(symbol)
        try:
            data = await self._get(f"/futures/usdt/positions/{contract}")
            size = float(data.get("size", 0) or 0)
            return PositionInfo(
                symbol=contract,
                qty=size,                                         # >0 long, <0 short
                avg_price=float(data.get("entry_price", 0) or 0),
                unrealized_pnl=float(data.get("unrealised_pnl", 0) or 0),
                leverage=int(data.get("leverage", 1) or 1),
            )
        except Exception as e:
            logger.warning("Gate get_position %s error: %s", contract, e)
        return PositionInfo(symbol=contract, qty=0.0, avg_price=0.0)

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _place(
        self,
        symbol: str,
        size: int,
        leverage: int = 1,
        reduce_only: bool = False,
    ) -> OrderResult:
        contract = self._to_contract(symbol)
        body: Dict[str, Any] = {
            "contract":   contract,
            "size":       size,          # positive=long, negative=short
            "price":      "0",           # 0 = market order
            "tif":        "ioc",         # immediate-or-cancel for market
            "leverage":   str(leverage),
        }
        if reduce_only:
            body["reduce_only"] = True

        try:
            data = await self._post("/futures/usdt/orders", body)
            order_id = str(data.get("id") or "")
            filled   = abs(float(data.get("size", 0) or 0))
            logger.info("Gate %s size=%d → order_id=%s", contract, size, order_id)
            return OrderResult(ok=True, order_id=order_id, filled_qty=filled, raw=data)
        except Exception as e:
            logger.error("Gate place order %s size=%d error: %s", contract, size, e)
            return OrderResult(ok=False, error=str(e))

    async def _get_position_size(self, symbol: str, long: bool) -> int:
        pos = await self.get_position(symbol)
        if long:
            return max(0, int(pos.qty))
        else:
            return max(0, int(-pos.qty))
