"""
Tape Tracker - Real-time trade monitoring (time & sales)

Purpose: Track every trade to detect:
- Aggressor side (buy vs sell)
- Buy/sell pressure
- Large trades (whales)
- Tape velocity

Integration: Use with MM Detector for pressure confirmation

Author: Keeper Memory AI - Phase 2
Date: November 13, 2025
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import statistics


def utc_now() -> datetime:
    """Get current UTC time with timezone"""
    return datetime.now(timezone.utc)


@dataclass
class Trade:
    """Single trade from tape"""
    symbol: str
    price: float
    size: float
    size_usd: float
    timestamp: datetime
    aggressor: str  # 'BUY' or 'SELL'
    is_large: bool  # Whale trade


@dataclass
class TapeMetrics:
    """Aggregated tape metrics for a symbol"""
    symbol: str
    window_sec: int
    
    # Counts
    total_trades: int
    buy_trades: int
    sell_trades: int
    large_trades: int
    
    # Volumes
    total_volume_usd: float
    buy_volume_usd: float
    sell_volume_usd: float
    
    # Ratios
    aggressor_ratio: float  # 0-1 (0=all sell, 1=all buy)
    buy_pressure: float     # buy_volume / total_volume
    
    # Velocity
    trades_per_sec: float
    avg_trade_size_usd: float
    
    # Timestamps
    first_trade: Optional[datetime]
    last_trade: Optional[datetime]


class TapeTracker:
    """
    Real-time tape (time & sales) monitoring
    
    Tracks every trade to analyze:
    - Aggressor side (who initiated: buyer or seller)
    - Buy vs sell pressure
    - Large trades (whales)
    - Trade velocity
    
    Use cases:
    - Detect accumulation/distribution
    - Identify aggressive buying/selling
    - Spot whale activity
    - Measure market urgency
    """
    
    def __init__(
        self,
        window_sec: int = 60,
        large_trade_threshold_usd: float = 1000.0,
        max_history_per_symbol: int = 1000
    ):
        """
        Args:
            window_sec: Time window for metrics (default 60s)
            large_trade_threshold_usd: Threshold for whale trades
            max_history_per_symbol: Max trades to keep in memory
        """
        self.window_sec = window_sec
        self.large_trade_threshold = large_trade_threshold_usd
        self.max_history = max_history_per_symbol
        
        # Trade history: symbol -> deque of trades
        self._trades: Dict[str, deque] = {}
        
        # Last metrics cache
        self._last_metrics: Dict[str, TapeMetrics] = {}
        
    async def on_trade(
        self,
        symbol: str,
        price: float,
        size: float,
        timestamp: Optional[datetime] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None
    ) -> None:
        """
        Process incoming trade
        
        Args:
            symbol: Trading pair
            price: Trade price
            size: Trade size (base currency)
            timestamp: Trade time (default: now)
            best_bid: Current best bid (for aggressor detection)
            best_ask: Current best ask (for aggressor detection)
        """
        if timestamp is None:
            timestamp = utc_now()
            
        # Calculate USD size (approximate)
        size_usd = price * size
        
        # Detect aggressor side
        aggressor = self._detect_aggressor(price, best_bid, best_ask)
        
        # Detect if large trade
        is_large = size_usd >= self.large_trade_threshold
        
        # Create trade object
        trade = Trade(
            symbol=symbol,
            price=price,
            size=size,
            size_usd=size_usd,
            timestamp=timestamp,
            aggressor=aggressor,
            is_large=is_large
        )
        
        # Store trade
        if symbol not in self._trades:
            self._trades[symbol] = deque(maxlen=self.max_history)
            
        self._trades[symbol].append(trade)
        
        # Clean old trades
        self._clean_old_trades(symbol)
        
    def _detect_aggressor(
        self,
        price: float,
        best_bid: Optional[float],
        best_ask: Optional[float]
    ) -> str:
        """
        Detect aggressor side
        
        Logic:
        - If price >= best_ask: Buyer aggressive (market buy)
        - If price <= best_bid: Seller aggressive (market sell)
        - Otherwise: Unknown
        
        Args:
            price: Trade price
            best_bid: Current best bid
            best_ask: Current best ask
            
        Returns:
            'BUY', 'SELL', or 'UNKNOWN'
        """
        if best_ask is not None and price >= best_ask:
            return 'BUY'
        elif best_bid is not None and price <= best_bid:
            return 'SELL'
        else:
            # If no book data, guess based on price movement
            # (fallback - not very accurate)
            return 'UNKNOWN'
    
    def _clean_old_trades(self, symbol: str) -> None:
        """Remove trades older than window"""
        if symbol not in self._trades:
            return
            
        cutoff = utc_now() - timedelta(seconds=self.window_sec)
        
        # Remove from left (oldest)
        while (
            self._trades[symbol] and 
            self._trades[symbol][0].timestamp < cutoff
        ):
            self._trades[symbol].popleft()
    
    def get_metrics(self, symbol: str, window_sec: Optional[int] = None) -> TapeMetrics:
        """
        Get aggregated tape metrics
        
        Args:
            symbol: Trading pair
            window_sec: Custom window (default: use instance window)
            
        Returns:
            TapeMetrics object with all aggregated data
        """
        if window_sec is None:
            window_sec = self.window_sec
            
        # Get trades in window
        trades = self._get_trades_in_window(symbol, window_sec)
        
        if not trades:
            # No trades - return empty metrics
            return TapeMetrics(
                symbol=symbol,
                window_sec=window_sec,
                total_trades=0,
                buy_trades=0,
                sell_trades=0,
                large_trades=0,
                total_volume_usd=0.0,
                buy_volume_usd=0.0,
                sell_volume_usd=0.0,
                aggressor_ratio=0.5,  # Neutral
                buy_pressure=0.5,     # Neutral
                trades_per_sec=0.0,
                avg_trade_size_usd=0.0,
                first_trade=None,
                last_trade=None
            )
        
        # Count trades
        total_trades = len(trades)
        buy_trades = sum(1 for t in trades if t.aggressor == 'BUY')
        sell_trades = sum(1 for t in trades if t.aggressor == 'SELL')
        large_trades = sum(1 for t in trades if t.is_large)
        
        # Volume calculations
        total_volume_usd = sum(t.size_usd for t in trades)
        buy_volume_usd = sum(t.size_usd for t in trades if t.aggressor == 'BUY')
        sell_volume_usd = sum(t.size_usd for t in trades if t.aggressor == 'SELL')
        
        # Ratios
        aggressor_ratio = buy_trades / total_trades if total_trades > 0 else 0.5
        buy_pressure = buy_volume_usd / total_volume_usd if total_volume_usd > 0 else 0.5
        
        # Velocity
        time_span = (trades[-1].timestamp - trades[0].timestamp).total_seconds()
        trades_per_sec = total_trades / time_span if time_span > 0 else 0.0
        avg_trade_size_usd = total_volume_usd / total_trades if total_trades > 0 else 0.0
        
        # Timestamps
        first_trade = trades[0].timestamp
        last_trade = trades[-1].timestamp
        
        metrics = TapeMetrics(
            symbol=symbol,
            window_sec=window_sec,
            total_trades=total_trades,
            buy_trades=buy_trades,
            sell_trades=sell_trades,
            large_trades=large_trades,
            total_volume_usd=total_volume_usd,
            buy_volume_usd=buy_volume_usd,
            sell_volume_usd=sell_volume_usd,
            aggressor_ratio=aggressor_ratio,
            buy_pressure=buy_pressure,
            trades_per_sec=trades_per_sec,
            avg_trade_size_usd=avg_trade_size_usd,
            first_trade=first_trade,
            last_trade=last_trade
        )
        
        # Cache
        self._last_metrics[symbol] = metrics
        
        return metrics
    
    def _get_trades_in_window(
        self, 
        symbol: str, 
        window_sec: int
    ) -> List[Trade]:
        """Get all trades within time window"""
        if symbol not in self._trades:
            return []
            
        cutoff = utc_now() - timedelta(seconds=window_sec)
        
        # Filter trades in window
        trades = [
            t for t in self._trades[symbol]
            if t.timestamp >= cutoff
        ]
        
        return trades
    
    def get_aggressor_ratio(self, symbol: str, window_sec: Optional[int] = None) -> float:
        """
        Get buy vs sell aggressor ratio
        
        Returns:
            0.0 = All sells (bearish)
            0.5 = Balanced
            1.0 = All buys (bullish)
        """
        metrics = self.get_metrics(symbol, window_sec)
        return metrics.aggressor_ratio
    
    def get_buy_pressure(self, symbol: str, window_sec: Optional[int] = None) -> float:
        """
        Get buy pressure (buy volume / total volume)
        
        Returns:
            0.0 = All sell volume (bearish)
            0.5 = Balanced
            1.0 = All buy volume (bullish)
        """
        metrics = self.get_metrics(symbol, window_sec)
        return metrics.buy_pressure
    
    def detect_aggressive_buying(
        self, 
        symbol: str,
        threshold: float = 0.65
    ) -> bool:
        """
        Detect if buyers are aggressive
        
        Args:
            threshold: Min aggressor ratio (default 0.65 = 65% buys)
            
        Returns:
            True if aggressive buying detected
        """
        ratio = self.get_aggressor_ratio(symbol)
        return ratio >= threshold
    
    def detect_aggressive_selling(
        self,
        symbol: str,
        threshold: float = 0.35
    ) -> bool:
        """
        Detect if sellers are aggressive
        
        Args:
            threshold: Max aggressor ratio (default 0.35 = 35% buys)
            
        Returns:
            True if aggressive selling detected
        """
        ratio = self.get_aggressor_ratio(symbol)
        return ratio <= threshold
    
    def get_large_trades(
        self,
        symbol: str,
        window_sec: Optional[int] = None
    ) -> List[Trade]:
        """Get all large (whale) trades in window"""
        trades = self._get_trades_in_window(symbol, window_sec or self.window_sec)
        return [t for t in trades if t.is_large]
    
    def get_summary(self, symbol: str) -> dict:
        """
        Get human-readable summary
        
        Returns dict with:
        - aggressor_ratio
        - buy_pressure
        - trades_per_sec
        - large_trades_count
        - interpretation (bullish/bearish/neutral)
        """
        metrics = self.get_metrics(symbol)
        
        # Interpret
        if metrics.aggressor_ratio > 0.6:
            interpretation = "BULLISH (aggressive buying)"
        elif metrics.aggressor_ratio < 0.4:
            interpretation = "BEARISH (aggressive selling)"
        else:
            interpretation = "NEUTRAL (balanced)"
        
        return {
            'symbol': symbol,
            'aggressor_ratio': round(metrics.aggressor_ratio, 3),
            'buy_pressure': round(metrics.buy_pressure, 3),
            'trades_per_sec': round(metrics.trades_per_sec, 2),
            'large_trades_count': metrics.large_trades,
            'interpretation': interpretation,
            'window_sec': metrics.window_sec
        }


# Global instance (singleton pattern)
_tape_tracker: Optional[TapeTracker] = None


def get_tape_tracker() -> TapeTracker:
    """Get global tape tracker instance"""
    global _tape_tracker
    if _tape_tracker is None:
        _tape_tracker = TapeTracker(
            window_sec=60,
            large_trade_threshold_usd=1000.0
        )
    return _tape_tracker