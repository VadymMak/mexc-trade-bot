# app/market_data/__init__.py
from __future__ import annotations

from typing import Optional, Any

from app.config.settings import settings

# MEXC реализация (как была)
try:
    from app.market_data.ws_client import MEXCWebSocketClient
except Exception:
    MEXCWebSocketClient = None  # type: ignore[misc,assignment]

try:
    from app.market_data.http_client import MexcHTTPClient  # если есть свой REST клиент
except Exception:
    MexcHTTPClient = None  # type: ignore[misc,assignment]

# Заглушки под Binance (этап 1)
from .binance_ws_stub import BinanceWSClient
from .binance_http_stub import BinanceHTTPClient


def make_ws_client(symbols: list[str], *, channels: Optional[list[str]] = None) -> Optional[Any]:
    """
    Возвращает WS-клиент под выбранного провайдера.
    Этап 1:
      - MEXC -> реальный клиент (protobuf)
      - BINANCE -> заглушка (ничего не делает)
    """
    if settings.is_mexc:
        if MEXCWebSocketClient is None:
            return None
        return MEXCWebSocketClient(symbols, channels=channels or ["BOOK_TICKER", "DEPTH_LIMIT"])
    # BINANCE (DEMO) – пока заглушка
    return BinanceWSClient(symbols, channels=channels or ["bookTicker", "depth"])


def make_http_client() -> Optional[Any]:
    """
    Возвращает REST-клиент маркет-даты под провайдера.
    Этап 1:
      - MEXC -> если есть MexcHTTPClient, вернём его; иначе None (используется прямой httpx в сервисах)
      - BINANCE -> заглушка, держащая base_url на testnet
    """
    if settings.is_mexc:
        if MexcHTTPClient is None:
            return None
        return MexcHTTPClient(
            base_url=getattr(settings, "rest_base_url", "https://api.mexc.com/api/v3")
        )
    # BINANCE DEMO
    return BinanceHTTPClient(
        base_url=getattr(settings, "binance_rest_base", "https://testnet.binance.vision")
    )
