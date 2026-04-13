"""
researcher/app/live/mexc_futures.py

MEXC Futures (Contract) API client.
Docs: https://mxcdevelop.github.io/apidocs/contract_v1_en/

Key differences from MEXC Spot:
  - Base URL: https://contract.mexc.com
  - Signing:  HMAC-SHA256(secret, api_key + timestamp_ms + body_json_or_query)
  - Headers:  ApiKey, Request-Time, Signature, Content-Type
  - Symbol:   BTC_USDT  (underscore, not BTCUSDT)
  - Side:     1=open_long, 2=close_short, 3=open_short, 4=close_long
  - Size:     vol = number of contracts (1 contract = 1 USDT notional on most pairs)
  - Leverage: sent per order, default 1 (cross-margin arb = no leverage)
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

_BASE = "https://contract.mexc.com"

# MEXC futures side codes
_OPEN_LONG   = 1
_CLOSE_SHORT = 2
_OPEN_SHORT  = 3
_CLOSE_LONG  = 4

# Order type: 1=limit, 5=market
_MARKET = 5


class MexcFutures:
    """
    MEXC Futures private client.

    Usage:
        async with MexcFutures(api_key, secret) as client:
            result = await client.open_long("BTC_USDT", usdt_size=10.0)
    """

    exchange_name = "mexc"

    def __init__(self, api_key: str, secret: str) -> None:
        self._key    = api_key
        self._secret = secret
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "MexcFutures":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Signing ────────────────────────────────────────────────────────────────

    def _sign(self, timestamp_ms: str, body: str = "") -> str:
        """
        MEXC futures signature:
          HMAC-SHA256(secret, api_key + timestamp_ms + body)
        body = JSON string for POST, query string for GET.
        """
        msg = self._key + timestamp_ms + body
        return hmac.new(
            self._secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, timestamp_ms: str, body: str = "") -> Dict[str, str]:
        return {
            "ApiKey":       self._key,
            "Request-Time": timestamp_ms,
            "Signature":    self._sign(timestamp_ms, body),
            "Content-Type": "application/json",
        }

    # ── HTTP ───────────────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert self._session, "Call inside async with block"
        ts = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        headers = self._headers(ts, query)
        url = f"{_BASE}{path}"
        async with self._session.get(url, params=params, headers=headers) as resp:
            data = await resp.json()
            if not data.get("success", True):
                raise RuntimeError(f"MEXC GET {path} error: {data}")
            return data

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        assert self._session, "Call inside async with block"
        ts = str(int(time.time() * 1000))
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._headers(ts, body_str)
        url = f"{_BASE}{path}"
        async with self._session.post(url, data=body_str, headers=headers) as resp:
            data = await resp.json()
            if not data.get("success", True):
                raise RuntimeError(f"MEXC POST {path} error: {data}")
            return data

    # ── Symbol helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_symbol(symbol: str) -> str:
        """Normalize: BTCUSDT → BTC_USDT, BTC_USDT → BTC_USDT"""
        s = symbol.upper().replace("-", "_")
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s

    @staticmethod
    def _usdt_to_vol(usdt_size: float) -> int:
        """
        MEXC: 1 contract = 1 USDT notional (for USDT-margined contracts).
        So vol = usdt_size for leverage=1.
        """
        return max(1, int(usdt_size))

    # ── FuturesClient interface ────────────────────────────────────────────────

    async def open_long(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        return await self._place(symbol, _OPEN_LONG, usdt_size, leverage)

    async def open_short(self, symbol: str, usdt_size: float, leverage: int = 1) -> OrderResult:
        return await self._place(symbol, _OPEN_SHORT, usdt_size, leverage)

    async def close_long(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        vol = self._usdt_to_vol(qty) if qty else await self._get_position_vol(symbol, long=True)
        if vol <= 0:
            return OrderResult(ok=True, filled_qty=0, error="no position to close")
        return await self._place(symbol, _CLOSE_LONG, vol, leverage=1, vol_override=vol)

    async def close_short(self, symbol: str, qty: Optional[float] = None) -> OrderResult:
        vol = self._usdt_to_vol(qty) if qty else await self._get_position_vol(symbol, long=False)
        if vol <= 0:
            return OrderResult(ok=True, filled_qty=0, error="no position to close")
        return await self._place(symbol, _CLOSE_SHORT, vol, leverage=1, vol_override=vol)

    async def get_position(self, symbol: str) -> PositionInfo:
        sym = self._to_symbol(symbol)
        try:
            data = await self._get("/api/v1/private/position/open_positions", {"symbol": sym})
            positions = data.get("data") or []
            for p in positions:
                if p.get("symbol") == sym:
                    side = p.get("positionType", 1)  # 1=long, 2=short
                    vol  = float(p.get("holdVol", 0) or 0)
                    qty  = vol if side == 1 else -vol
                    return PositionInfo(
                        symbol=sym,
                        qty=qty,
                        avg_price=float(p.get("openAvgPrice", 0) or 0),
                        unrealized_pnl=float(p.get("unrealisedPnl", 0) or 0),
                        leverage=int(p.get("leverage", 1) or 1),
                    )
        except Exception as e:
            logger.warning("MEXC get_position %s error: %s", sym, e)
        return PositionInfo(symbol=sym, qty=0.0, avg_price=0.0)

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _place(
        self,
        symbol: str,
        side: int,
        usdt_size: float,
        leverage: int,
        vol_override: Optional[int] = None,
    ) -> OrderResult:
        sym = self._to_symbol(symbol)
        vol = vol_override if vol_override is not None else self._usdt_to_vol(usdt_size)
        body: Dict[str, Any] = {
            "symbol":     sym,
            "side":       side,
            "openType":   2,       # 2 = cross margin (safe for arb: no isolated liquidation)
            "type":       _MARKET, # market order
            "vol":        vol,
            "leverage":   leverage,
        }
        try:
            data = await self._post("/api/v1/private/order/submit", body)
            order_id = str(data.get("data") or "")
            logger.info("MEXC %s side=%d vol=%d → order_id=%s", sym, side, vol, order_id)
            return OrderResult(ok=True, order_id=order_id, filled_qty=float(vol), raw=data)
        except Exception as e:
            logger.error("MEXC place order %s side=%d error: %s", sym, side, e)
            return OrderResult(ok=False, error=str(e))

    async def _get_position_vol(self, symbol: str, long: bool) -> int:
        pos = await self.get_position(symbol)
        if long:
            return max(0, int(pos.qty))
        else:
            return max(0, int(-pos.qty))
