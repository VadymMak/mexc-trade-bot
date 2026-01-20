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
    from .mexc_http import MexcHttp  # Стандартизировано: класс MexcHttp для соответствия ожидаемому импорту
except Exception:
    MexcHttp = None  # type: ignore[misc,assignment]

# ── MarketDataHub (real-time data for trading engine) ─────────────────────────
try:
    from app.market_data.market_data_hub import (
        MarketDataHub,
        get_market_data_hub,
        reset_market_data_hub,
    )
except Exception:
    MarketDataHub = None  # type: ignore[misc,assignment]
    get_market_data_hub = None  # type: ignore[misc,assignment]
    reset_market_data_hub = None  # type: ignore[misc,assignment]


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
      - MEXC -> если есть MexcHttp, вернём его; иначе None (используется прямой httpx в сервисах)
      - BINANCE -> заглушка, держащая base_url на testnet
    """
    if settings.is_mexc:
        if MexcHttp is None:
            return None
        return MexcHttp(
            base_url=getattr(settings, "rest_base_url", "https://api.mexc.com")
        )
    # BINANCE DEMO
    return BinanceHTTPClient(
        base_url=getattr(settings, "binance_rest_base", "https://testnet.binance.vision")
    )