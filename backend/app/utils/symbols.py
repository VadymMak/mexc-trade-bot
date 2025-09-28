# backend/app/utils/symbols.py

from typing import Iterable

QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "BTC", "ETH")


def normalize_symbol(sym: str) -> str:
    """
    Canonical UI/DB form:
      - uppercase
      - no separators (BTC_USDT -> BTCUSDT)
    """
    s = (sym or "").strip().upper()
    return s.replace("_", "")


def is_quote_symbol(sym: str, quotes: Iterable[str] = QUOTE_SUFFIXES) -> bool:
    s = (sym or "").strip().upper().replace("_", "")
    return any(s.endswith(q) for q in quotes)


def ui_symbol(sym: str) -> str:
    """
    Alias of normalize_symbol() to be explicit at call sites where the target
    consumer is UI/DB (no separators).
    """
    return normalize_symbol(sym)


def mexc_ws_symbol(sym: str) -> str:
    """
    MEXC WS wants BASE_QUOTE with an underscore.
      e.g. PLBUSDT -> PLB_USDT
    """
    s = normalize_symbol(sym)  # now s like 'PLBUSDT'
    for q in QUOTE_SUFFIXES:
        if s.endswith(q):
            base = s[:-len(q)]
            return f"{base}_{q}"
    return s  # fallback (already normalized)


def gate_ws_symbol(sym: str) -> str:
    """
    Gate.io WS also commonly uses 'BASE_QUOTE' with underscore.
      e.g. BTCUSDT -> BTC_USDT
    """
    s = normalize_symbol(sym)
    for q in QUOTE_SUFFIXES:
        if s.endswith(q):
            base = s[:-len(q)]
            return f"{base}_{q}"
    return s


def binance_ws_symbol(sym: str) -> str:
    """
    Binance WS/REST uses canonical form without underscore.
      e.g. BTCUSDT -> BTCUSDT
    """
    return normalize_symbol(sym)


def from_ws_symbol(sym: str) -> str:
    """
    Convert any incoming WS symbol to canonical UI/DB form.
      e.g. 'SOL_USDT' -> 'SOLUSDT'
    """
    return normalize_symbol(sym)
