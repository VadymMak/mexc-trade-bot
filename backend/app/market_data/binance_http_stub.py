# app/market_data/binance_http_stub.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import time
import httpx


class BinanceHTTPClient:
    """
    Простая REST-обёртка под Binance testnet (демо).
    Реализованы только методы, которые сейчас зовём из сервисов (ticker/depth).
    """
    def __init__(self, base_url: str = "https://testnet.binance.vision") -> None:
        self.base_url = base_url.rstrip("/")

    async def get_ticker_book(self, symbol: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/api/v3/ticker/bookTicker"
        try:
            async with httpx.AsyncClient(timeout=5.0, headers={"Accept": "application/json"}) as cli:
                r = await cli.get(url, params={"symbol": symbol})
            if r.status_code != 200:
                return None
            j = r.json()
            return {
                "symbol": symbol,
                "bid": float(j.get("bidPrice") or 0.0),
                "ask": float(j.get("askPrice") or 0.0),
                "bidQty": float(j.get("bidQty") or 0.0),
                "askQty": float(j.get("askQty") or 0.0),
                "ts_ms": int(time.time() * 1000),
            }
        except Exception:
            return None

    async def get_depth(self, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/api/v3/depth"
        try:
            async with httpx.AsyncClient(timeout=5.0, headers={"Accept": "application/json"}) as cli:
                r = await cli.get(url, params={"symbol": symbol, "limit": limit})
            if r.status_code != 200:
                return None
            j = r.json()
            def _levels(raw: List[List[str]]) -> List[Tuple[float, float]]:
                out: List[Tuple[float, float]] = []
                for it in raw or []:
                    try:
                        p = float(it[0]); q = float(it[1])
                        if p > 0 and q > 0:
                            out.append((p, q))
                    except Exception:
                        continue
                return out[:limit]
            return {
                "symbol": symbol,
                "bids": _levels(j.get("bids") or []),
                "asks": _levels(j.get("asks") or []),
                "ts_ms": int(time.time() * 1000),
            }
        except Exception:
            return None
