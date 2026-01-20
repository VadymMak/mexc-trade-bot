# app/market_data/market_data_hub.py
"""
MarketDataHub - Centralized real-time market data manager.

Provides instant access to market data for trading engine (no HTTP overhead).
Fed by WebSocket callbacks from ws_client.py.

Architecture:
    WebSocket → ws_client.py → MarketDataHub → Trading Engine
                                    ↓
                              In-memory snapshots
                              (< 10ms access)
"""
from __future__ import annotations

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class SymbolSnapshot:
    """
    Real-time market data snapshot for one symbol.
    
    Updated by WebSocket callbacks, read by trading engine.
    All times in milliseconds.
    """
    symbol: str
    
    # ═══════════════════════════════════════════════════════════════
    # TOP-OF-BOOK (from bookTicker)
    # ═══════════════════════════════════════════════════════════════
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    
    # ═══════════════════════════════════════════════════════════════
    # FULL ORDERBOOK (from depth updates) - top 10 levels
    # ═══════════════════════════════════════════════════════════════
    bids: List[Tuple[float, float]] = field(default_factory=list)  # [(price, qty), ...]
    asks: List[Tuple[float, float]] = field(default_factory=list)
    
    # ═══════════════════════════════════════════════════════════════
    # DERIVED METRICS (calculated on update)
    # ═══════════════════════════════════════════════════════════════
    mid_price: float = 0.0
    spread_abs: float = 0.0
    spread_bps: float = 0.0
    imbalance: float = 0.5  # bid_qty / (bid_qty + ask_qty)
    
    # Depth at bps levels (USD)
    depth5_bid_usd: float = 0.0
    depth5_ask_usd: float = 0.0
    depth10_bid_usd: float = 0.0
    depth10_ask_usd: float = 0.0
    
    # ═══════════════════════════════════════════════════════════════
    # TAPE METRICS (from deals/trades)
    # ═══════════════════════════════════════════════════════════════
    usd_per_min: float = 0.0
    trades_per_min: float = 0.0
    recent_trades: List[Tuple[float, float, int]] = field(default_factory=list)  # [(price, qty, ts_ms), ...]
    
    # ═══════════════════════════════════════════════════════════════
    # STALENESS TRACKING
    # ═══════════════════════════════════════════════════════════════
    last_book_ticker_ms: int = 0
    last_depth_update_ms: int = 0
    last_trade_update_ms: int = 0
    update_count: int = 0
    
    @property
    def data_age_ms(self) -> int:
        """Age of most recent book data in milliseconds."""
        latest = max(self.last_book_ticker_ms, self.last_depth_update_ms)
        if latest == 0:
            return 999999  # No data yet
        return int(time.time() * 1000) - latest
    
    @property
    def is_fresh(self) -> bool:
        """Data is fresh if < 1000ms old."""
        return self.data_age_ms < 1000
    
    @property
    def is_valid(self) -> bool:
        """Data is valid if we have bid/ask and it's reasonably fresh."""
        return (
            self.best_bid > 0 and 
            self.best_ask > 0 and 
            self.best_bid < self.best_ask and
            self.data_age_ms < 5000
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "symbol": self.symbol,
            "bid": self.best_bid,
            "ask": self.best_ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "mid": self.mid_price,
            "spread_bps": self.spread_bps,
            "imbalance": self.imbalance,
            "depth5_bid_usd": self.depth5_bid_usd,
            "depth5_ask_usd": self.depth5_ask_usd,
            "depth10_bid_usd": self.depth10_bid_usd,
            "depth10_ask_usd": self.depth10_ask_usd,
            "usd_per_min": self.usd_per_min,
            "trades_per_min": self.trades_per_min,
            "data_age_ms": self.data_age_ms,
            "is_fresh": self.is_fresh,
            "is_valid": self.is_valid,
            "update_count": self.update_count,
        }


class MarketDataHub:
    """
    Centralized real-time market data manager.
    
    Responsibilities:
    - Stores real-time orderbook state per symbol
    - Provides instant data access for trading engine (no HTTP!)
    - Calculates derived metrics (imbalance, depth@bps)
    
    Usage in trading engine:
        hub = get_market_data_hub()
        snap = hub.get_snapshot("VETUSDT")
        if snap and snap.is_fresh:
            bid, ask = snap.best_bid, snap.best_ask
            imbalance = snap.imbalance
    """
    
    def __init__(self, max_trade_history: int = 100):
        self._snapshots: Dict[str, SymbolSnapshot] = {}
        self._max_trade_history = max_trade_history
        self._lock = asyncio.Lock()
        
        # Stats
        self._total_book_ticker_updates = 0
        self._total_depth_updates = 0
        self._total_tape_updates = 0
        
        logger.info("📊 MarketDataHub initialized")
    
    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API - Called by Trading Engine
    # ═══════════════════════════════════════════════════════════════
    
    def get_snapshot(self, symbol: str) -> Optional[SymbolSnapshot]:
        """
        Get current market data snapshot (instant, no HTTP).
        
        Returns None if symbol not tracked.
        Check snap.is_fresh before using for trading decisions.
        """
        return self._snapshots.get(symbol.upper())
    
    def get_all_snapshots(self) -> Dict[str, SymbolSnapshot]:
        """Get all tracked symbol snapshots."""
        return dict(self._snapshots)
    
    def get_symbols(self) -> List[str]:
        """Get list of tracked symbols."""
        return list(self._snapshots.keys())
    
    def get_fresh_symbols(self) -> List[str]:
        """Get list of symbols with fresh data."""
        return [s for s, snap in self._snapshots.items() if snap.is_fresh]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get hub statistics."""
        return {
            "tracked_symbols": len(self._snapshots),
            "fresh_symbols": len(self.get_fresh_symbols()),
            "total_book_ticker_updates": self._total_book_ticker_updates,
            "total_depth_updates": self._total_depth_updates,
            "total_tape_updates": self._total_tape_updates,
        }
    
    # ═══════════════════════════════════════════════════════════════
    # CALLBACKS - Called by ws_client.py
    # ═══════════════════════════════════════════════════════════════
    
    async def on_book_ticker(
        self,
        symbol: str,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
        ts_ms: Optional[int] = None
    ) -> None:
        """
        Handle bookTicker update from WebSocket.
        
        Called by ws_client._bt_cb() → feeds this hub.
        """
        sym = symbol.upper()
        ts = ts_ms or int(time.time() * 1000)
        
        snap = self._snapshots.get(sym)
        if snap is None:
            snap = SymbolSnapshot(symbol=sym)
            self._snapshots[sym] = snap
        
        # Update top-of-book
        snap.best_bid = bid
        snap.best_ask = ask
        snap.bid_qty = bid_qty
        snap.ask_qty = ask_qty
        
        # Calculate derived metrics
        snap.mid_price = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
        snap.spread_abs = ask - bid if (bid > 0 and ask > 0) else 0.0
        snap.spread_bps = (snap.spread_abs / snap.mid_price * 10000) if snap.mid_price > 0 else 0.0
        
        # Imbalance from top-of-book quantities
        total_qty = bid_qty + ask_qty
        snap.imbalance = (bid_qty / total_qty) if total_qty > 0 else 0.5
        
        # Timestamps
        snap.last_book_ticker_ms = ts
        snap.update_count += 1
        
        self._total_book_ticker_updates += 1
    
    async def on_depth(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        ts_ms: Optional[int] = None
    ) -> None:
        """
        Handle depth (orderbook) update from WebSocket.
        
        Called by ws_client._depth_cb() → feeds this hub.
        
        Args:
            symbol: Trading pair (e.g., "VETUSDT")
            bids: List of (price, qty) tuples, sorted high→low
            asks: List of (price, qty) tuples, sorted low→high
            ts_ms: Exchange timestamp in milliseconds
        """
        sym = symbol.upper()
        ts = ts_ms or int(time.time() * 1000)
        
        snap = self._snapshots.get(sym)
        if snap is None:
            snap = SymbolSnapshot(symbol=sym)
            self._snapshots[sym] = snap
        
        # Store orderbook levels (top 10)
        snap.bids = bids[:10] if bids else []
        snap.asks = asks[:10] if asks else []
        
        # Update top-of-book from depth if available
        if bids and asks:
            snap.best_bid = bids[0][0]
            snap.best_ask = asks[0][0]
            snap.bid_qty = bids[0][1]
            snap.ask_qty = asks[0][1]
            
            # Recalculate derived metrics
            snap.mid_price = (snap.best_bid + snap.best_ask) / 2.0
            snap.spread_abs = snap.best_ask - snap.best_bid
            snap.spread_bps = (snap.spread_abs / snap.mid_price * 10000) if snap.mid_price > 0 else 0.0
            
            total_qty = snap.bid_qty + snap.ask_qty
            snap.imbalance = (snap.bid_qty / total_qty) if total_qty > 0 else 0.5
            
            # Calculate depth at bps levels
            snap.depth5_bid_usd = self._calc_depth_usd(bids, snap.mid_price, 5, is_bid=True)
            snap.depth5_ask_usd = self._calc_depth_usd(asks, snap.mid_price, 5, is_bid=False)
            snap.depth10_bid_usd = self._calc_depth_usd(bids, snap.mid_price, 10, is_bid=True)
            snap.depth10_ask_usd = self._calc_depth_usd(asks, snap.mid_price, 10, is_bid=False)
        
        # Timestamps
        snap.last_depth_update_ms = ts
        snap.update_count += 1
        
        self._total_depth_updates += 1
    
    async def on_tape(
        self,
        symbol: str,
        usd_per_min: float,
        trades_per_min: float,
        trades: Optional[List[Tuple[float, float, int]]] = None,
        ts_ms: Optional[int] = None
    ) -> None:
        """
        Handle tape (trades/deals) update from WebSocket.
        
        Called by ws_client.update_tape_metrics() → feeds this hub.
        
        Args:
            symbol: Trading pair
            usd_per_min: USD volume per minute
            trades_per_min: Trade count per minute
            trades: Optional list of (price, qty, ts_ms) tuples
            ts_ms: Timestamp
        """
        sym = symbol.upper()
        ts = ts_ms or int(time.time() * 1000)
        
        snap = self._snapshots.get(sym)
        if snap is None:
            snap = SymbolSnapshot(symbol=sym)
            self._snapshots[sym] = snap
        
        snap.usd_per_min = usd_per_min
        snap.trades_per_min = trades_per_min
        
        if trades:
            # Append and trim to max history
            snap.recent_trades.extend(trades)
            if len(snap.recent_trades) > self._max_trade_history:
                snap.recent_trades = snap.recent_trades[-self._max_trade_history:]
        
        snap.last_trade_update_ms = ts
        self._total_tape_updates += 1
    
    # ═══════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════
    
    def _calc_depth_usd(
        self,
        levels: List[Tuple[float, float]],
        mid: float,
        bps: int,
        is_bid: bool
    ) -> float:
        """
        Calculate USD depth within bps of mid price.
        
        Args:
            levels: List of (price, qty) tuples
            mid: Mid price
            bps: Basis points from mid (e.g., 5 = ±0.05%)
            is_bid: True for bid side, False for ask side
        
        Returns:
            Total USD depth within the band
        """
        if not levels or mid <= 0:
            return 0.0
        
        band = bps / 10000.0
        total_usd = 0.0
        
        for price, qty in levels:
            if price <= 0 or qty <= 0:
                continue
            
            if is_bid:
                # Bid levels: include if price >= mid * (1 - band)
                if price < mid * (1 - band):
                    break  # Sorted high→low, so stop here
            else:
                # Ask levels: include if price <= mid * (1 + band)
                if price > mid * (1 + band):
                    break  # Sorted low→high, so stop here
            
            total_usd += price * qty
        
        return total_usd
    
    def reset(self) -> None:
        """Reset all data (use with caution)."""
        self._snapshots.clear()
        self._total_book_ticker_updates = 0
        self._total_depth_updates = 0
        self._total_tape_updates = 0
        logger.info("📊 MarketDataHub reset")
    
    def remove_symbol(self, symbol: str) -> bool:
        """Remove a symbol from tracking."""
        sym = symbol.upper()
        if sym in self._snapshots:
            del self._snapshots[sym]
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_hub_instance: Optional[MarketDataHub] = None


def get_market_data_hub() -> MarketDataHub:
    """
    Get the global MarketDataHub singleton.
    
    Usage:
        from app.market_data.market_data_hub import get_market_data_hub
        
        hub = get_market_data_hub()
        snap = hub.get_snapshot("VETUSDT")
    """
    global _hub_instance
    if _hub_instance is None:
        _hub_instance = MarketDataHub()
    return _hub_instance


def reset_market_data_hub() -> None:
    """Reset the global hub instance (for testing)."""
    global _hub_instance
    if _hub_instance is not None:
        _hub_instance.reset()
    _hub_instance = None