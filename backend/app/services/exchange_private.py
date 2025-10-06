# app/services/exchange_private.py
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple
from datetime import datetime  # NEW

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


# ───────────────────────── Factory (providers added one-by-one) ─────────────────────────

def get_private_client() -> ExchangePrivate:
    """
    Returns an ExchangePrivate implementation based on settings.active_provider / active_mode.
    Gate will be implemented first; others come next.
    """
    prov = Provider.from_settings()
    if prov == Provider.GATE:
        # imported lazily to avoid hard dependency until the file exists
        from app.services.gate_private import GatePrivate  # type: ignore
        return GatePrivate()
    if prov == Provider.BINANCE:
        # to be implemented next (binance_private.py)
        raise NotImplementedError("Binance private client not implemented yet.")
    # default: MEXC
    raise NotImplementedError("MEXC private client not implemented yet.")
