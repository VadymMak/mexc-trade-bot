"""
Enhanced Book Tracker - Advanced order book analysis

Purpose: Extend basic book tracking with:
- Order lifetime tracking (dwell time)
- Spoofing detection (fake orders)
- Spread stability analysis
- Order flow patterns

Integration: Use with Smart Executor for execution quality

Author: Keeper Memory AI - Phase 2
Date: November 13, 2025
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
import statistics


def utc_now() -> datetime:
    """Get current UTC time with timezone"""
    return datetime.now(timezone.utc)


@dataclass
class OrderLevel:
    """Single order book level"""
    price: float
    size: float
    side: str  # 'bid' or 'ask'
    first_seen: datetime
    last_seen: datetime
    update_count: int = 0
    
    @property
    def lifetime_sec(self) -> float:
        """How long this order has been in book"""
        return (self.last_seen - self.first_seen).total_seconds()


@dataclass
class SpoofingSignal:
    """Detected spoofing activity"""
    symbol: str
    timestamp: datetime
    price: float
    size: float
    side: str
    reason: str  # Why flagged as spoof
    confidence: float  # 0-1


@dataclass
class BookMetrics:
    """Aggregated book metrics"""
    symbol: str
    
    # Lifetime stats
    avg_order_lifetime_sec: float
    median_order_lifetime_sec: float
    short_lived_orders_pct: float  # % orders < 1 sec
    
    # Spoofing
    spoofing_score: float  # 0-1 (0=clean, 1=lots of spoofing)
    spoof_orders_detected: int
    
    # Spread stability
    spread_stability_score: float  # 0-1 (0=volatile, 1=stable)
    avg_spread_bps: float
    spread_changes_per_min: float
    
    # Order flow
    book_refresh_rate: float  # Hz
    avg_update_count: float
    
    # Timestamps
    window_sec: int
    calculated_at: datetime


class EnhancedBookTracker:
    """
    Enhanced Order Book Tracker
    
    Tracks order book with advanced features:
    - Order lifetime (how long orders stay)
    - Spoofing detection (fake large orders)
    - Spread stability
    - Update frequency
    
    Use cases:
    - Detect market manipulation
    - Assess book quality
    - Find stable MM patterns
    - Avoid fake liquidity
    """
    
    def __init__(
        self,
        window_sec: int = 300,  # 5 minutes
        spoof_size_multiplier: float = 10.0,
        spoof_lifetime_max: float = 1.0,
        spoof_update_rate_min: float = 5.0
    ):
        """
        Args:
            window_sec: Analysis window
            spoof_size_multiplier: Order > X * normal = spoof
            spoof_lifetime_max: Order < X sec = spoof
            spoof_update_rate_min: Updates > X Hz = spoof
        """
        self.window_sec = window_sec
        self.spoof_size_mult = spoof_size_multiplier
        self.spoof_lifetime_max = spoof_lifetime_max
        self.spoof_update_min = spoof_update_rate_min
        
        # Current order levels: symbol -> {price: OrderLevel}
        self._bid_levels: Dict[str, Dict[float, OrderLevel]] = defaultdict(dict)
        self._ask_levels: Dict[str, Dict[float, OrderLevel]] = defaultdict(dict)
        
        # Historical orders (for lifetime analysis)
        self._order_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # Spoofing signals
        self._spoof_signals: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        
        # Spread history
        self._spread_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=300))
        
    def on_book_update(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],  # [(price, size), ...]
        asks: List[Tuple[float, float]],
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Process order book update
        
        Args:
            symbol: Trading pair
            bids: List of (price, size) for bids
            asks: List of (price, size) for asks
            timestamp: Update time (default: now)
        """
        if timestamp is None:
            timestamp = utc_now()
        
        # Update bid levels
        self._update_levels(symbol, bids, 'bid', timestamp)
        
        # Update ask levels
        self._update_levels(symbol, asks, 'ask', timestamp)
        
        # Track spread
        if bids and asks:
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid = (best_bid + best_ask) / 2
            spread_bps = ((best_ask - best_bid) / mid) * 10000
            self._spread_history[symbol].append((timestamp, spread_bps))
        
        # Clean old data
        self._clean_old_data(symbol)
    
    def _update_levels(
        self,
        symbol: str,
        levels: List[Tuple[float, float]],
        side: str,
        timestamp: datetime
    ) -> None:
        """Update order levels and detect changes"""
        
        levels_dict = self._bid_levels[symbol] if side == 'bid' else self._ask_levels[symbol]
        
        # Get current prices
        current_prices = {price for price, size in levels}
        existing_prices = set(levels_dict.keys())
        
        # Removed orders (existed before, not now)
        removed_prices = existing_prices - current_prices
        for price in removed_prices:
            order = levels_dict[price]
            order.last_seen = timestamp
            
            # Move to history
            self._order_history[symbol].append(order)
            
            # Check if spoof
            if self._is_spoof(order):
                signal = SpoofingSignal(
                    symbol=symbol,
                    timestamp=timestamp,
                    price=price,
                    size=order.size,
                    side=side,
                    reason=self._get_spoof_reason(order),
                    confidence=0.8
                )
                self._spoof_signals[symbol].append(signal)
            
            # Remove from active
            del levels_dict[price]
        
        # Update or add orders
        for price, size in levels:
            if price in levels_dict:
                # Existing order - update
                order = levels_dict[price]
                order.last_seen = timestamp
                order.size = size
                order.update_count += 1
            else:
                # New order
                order = OrderLevel(
                    price=price,
                    size=size,
                    side=side,
                    first_seen=timestamp,
                    last_seen=timestamp,
                    update_count=1
                )
                levels_dict[price] = order
    
    def _is_spoof(self, order: OrderLevel) -> bool:
        """Check if order looks like spoofing"""
        
        # Check 1: Very short lifetime
        if order.lifetime_sec < self.spoof_lifetime_max:
            return True
        
        # Check 2: High update frequency
        if order.lifetime_sec > 0:
            update_rate = order.update_count / order.lifetime_sec
            if update_rate > self.spoof_update_min:
                return True
        
        return False
    
    def _get_spoof_reason(self, order: OrderLevel) -> str:
        """Get reason why order is flagged as spoof"""
        reasons = []
        
        if order.lifetime_sec < self.spoof_lifetime_max:
            reasons.append(f"short lifetime ({order.lifetime_sec:.2f}s)")
        
        if order.lifetime_sec > 0:
            update_rate = order.update_count / order.lifetime_sec
            if update_rate > self.spoof_update_min:
                reasons.append(f"high update rate ({update_rate:.1f} Hz)")
        
        return ", ".join(reasons)
    
    def _clean_old_data(self, symbol: str) -> None:
        """Remove data older than window"""
        cutoff = utc_now() - timedelta(seconds=self.window_sec)
        
        # Clean history
        while (
            self._order_history[symbol] and
            self._order_history[symbol][0].last_seen < cutoff
        ):
            self._order_history[symbol].popleft()
        
        # Clean spoof signals
        while (
            self._spoof_signals[symbol] and
            self._spoof_signals[symbol][0].timestamp < cutoff
        ):
            self._spoof_signals[symbol].popleft()
        
        # Clean spread history
        while (
            self._spread_history[symbol] and
            self._spread_history[symbol][0][0] < cutoff
        ):
            self._spread_history[symbol].popleft()
    
    def get_metrics(self, symbol: str) -> BookMetrics:
        """Get aggregated book metrics"""
        
        history = list(self._order_history[symbol])
        
        if not history:
            # No data
            return BookMetrics(
                symbol=symbol,
                avg_order_lifetime_sec=0.0,
                median_order_lifetime_sec=0.0,
                short_lived_orders_pct=0.0,
                spoofing_score=0.0,
                spoof_orders_detected=0,
                spread_stability_score=1.0,
                avg_spread_bps=0.0,
                spread_changes_per_min=0.0,
                book_refresh_rate=0.0,
                avg_update_count=0.0,
                window_sec=self.window_sec,
                calculated_at=utc_now()
            )
        
        # Lifetime stats
        lifetimes = [o.lifetime_sec for o in history]
        avg_lifetime = statistics.mean(lifetimes)
        median_lifetime = statistics.median(lifetimes)
        short_lived_pct = sum(1 for lt in lifetimes if lt < 1.0) / len(lifetimes)
        
        # Spoofing
        spoof_count = len(self._spoof_signals[symbol])
        spoofing_score = min(1.0, spoof_count / 10.0)  # 10+ spoofs = score 1.0
        
        # Spread stability
        spreads = [s for _, s in self._spread_history[symbol]]
        if spreads:
            avg_spread = statistics.mean(spreads)
            spread_std = statistics.stdev(spreads) if len(spreads) > 1 else 0.0
            # Stability: low std = high stability
            spread_stability = max(0.0, 1.0 - (spread_std / (avg_spread + 0.1)))
            
            # Spread change rate
            if len(self._spread_history[symbol]) > 1:
                time_span = (
                    self._spread_history[symbol][-1][0] - 
                    self._spread_history[symbol][0][0]
                ).total_seconds() / 60.0  # minutes
                
                changes = sum(
                    1 for i in range(1, len(spreads))
                    if abs(spreads[i] - spreads[i-1]) > 0.5  # > 0.5 bps change
                )
                spread_changes_per_min = changes / time_span if time_span > 0 else 0.0
            else:
                spread_changes_per_min = 0.0
        else:
            avg_spread = 0.0
            spread_stability = 1.0
            spread_changes_per_min = 0.0
        
        # Order flow
        update_counts = [o.update_count for o in history]
        avg_updates = statistics.mean(update_counts) if update_counts else 0.0
        
        # Refresh rate (orders added per second)
        if history:
            time_span = (history[-1].last_seen - history[0].first_seen).total_seconds()
            refresh_rate = len(history) / time_span if time_span > 0 else 0.0
        else:
            refresh_rate = 0.0
        
        return BookMetrics(
            symbol=symbol,
            avg_order_lifetime_sec=avg_lifetime,
            median_order_lifetime_sec=median_lifetime,
            short_lived_orders_pct=short_lived_pct,
            spoofing_score=spoofing_score,
            spoof_orders_detected=spoof_count,
            spread_stability_score=spread_stability,
            avg_spread_bps=avg_spread,
            spread_changes_per_min=spread_changes_per_min,
            book_refresh_rate=refresh_rate,
            avg_update_count=avg_updates,
            window_sec=self.window_sec,
            calculated_at=utc_now()
        )
    
    def get_spoofing_signals(self, symbol: str) -> List[SpoofingSignal]:
        """Get recent spoofing signals"""
        return list(self._spoof_signals[symbol])
    
    def get_summary(self, symbol: str) -> dict:
        """Get human-readable summary"""
        metrics = self.get_metrics(symbol)
        
        # Interpret spoofing
        if metrics.spoofing_score < 0.2:
            spoof_level = "LOW (clean book)"
        elif metrics.spoofing_score < 0.5:
            spoof_level = "MODERATE (some spoofing)"
        else:
            spoof_level = "HIGH (lots of spoofing!)"
        
        # Interpret spread stability
        if metrics.spread_stability_score > 0.8:
            spread_level = "STABLE (good)"
        elif metrics.spread_stability_score > 0.5:
            spread_level = "MODERATE"
        else:
            spread_level = "VOLATILE (risky)"
        
        return {
            'symbol': symbol,
            'avg_order_lifetime': round(metrics.avg_order_lifetime_sec, 2),
            'spoofing_score': round(metrics.spoofing_score, 3),
            'spoofing_level': spoof_level,
            'spoof_count': metrics.spoof_orders_detected,
            'spread_stability': round(metrics.spread_stability_score, 3),
            'spread_level': spread_level,
            'avg_spread_bps': round(metrics.avg_spread_bps, 2),
            'book_refresh_rate': round(metrics.book_refresh_rate, 2),
            'window_sec': metrics.window_sec
        }


# Global instance
_enhanced_book_tracker: Optional[EnhancedBookTracker] = None


def get_enhanced_book_tracker() -> EnhancedBookTracker:
    """Get global enhanced book tracker instance"""
    global _enhanced_book_tracker
    if _enhanced_book_tracker is None:
        _enhanced_book_tracker = EnhancedBookTracker(
            window_sec=300,
            spoof_size_multiplier=10.0,
            spoof_lifetime_max=1.0
        )
    return _enhanced_book_tracker