"""
Market Maker Detector - Identify and track MM patterns

Purpose: Detect market maker behavior to optimize entry/exit:
- Identify MM boundaries (support/resistance)
- Measure MM order size (capacity)
- Calculate MM refresh rate
- Provide confidence score

Author: Keeper Memory AI - Phase 2
Date: November 13, 2025
"""

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import statistics

# Phase 2: Tape integration
try:
    from app.services.tape_tracker import get_tape_tracker
    TAPE_AVAILABLE = True
except ImportError:
    TAPE_AVAILABLE = False


def utc_now() -> datetime:
    """Get current UTC time with timezone"""
    return datetime.now(timezone.utc)


@dataclass
class OrderBookSnapshot:
    """Order book snapshot for analysis"""
    symbol: str
    timestamp: datetime
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    mid_price: float
    spread_bps: float


@dataclass
class MMPattern:
    """Detected market maker pattern"""
    symbol: str
    detected_at: datetime
    
    # MM Boundaries
    mm_lower_bound: Optional[float]  # Where MM buys (support)
    mm_upper_bound: Optional[float]  # Where MM sells (resistance)
    
    # MM Behavior
    mm_avg_order_size: float         # Typical MM order size (USD)
    mm_refresh_rate: float           # How often MM updates (Hz)
    mm_spread_bps: float             # Typical spread maintained
    
    # Quality metrics
    mm_confidence: float             # Detection confidence (0-1)
    samples_count: int               # Number of observations
    
    # Recommendations
    best_entry_price: Optional[float]   # Recommended BUY price
    best_exit_price: Optional[float]    # Recommended SELL price
    safe_order_size_usd: float          # Safe size to not scare MM
    
    # Metadata
    window_sec: int
    last_updated: datetime


class MMDetector:
    """
    Market Maker Pattern Detector
    
    Analyzes order book to identify MM behavior:
    - Where does MM place orders? (boundaries)
    - What sizes? (capacity)
    - How often updates? (refresh rate)
    - How confident are we? (quality score)
    
    Use cases:
    - Find best entry/exit prices
    - Determine safe order size
    - Avoid scaring MM away
    - Time entries when MM is active
    """
    
    def __init__(
        self,
        window_sec: int = 300,  # 5 minutes
        min_samples: int = 20,
        price_cluster_threshold: float = 0.0001,  # 1 bps
        min_confidence: float = 0.7
    ):
        """
        Args:
            window_sec: Analysis window (default 5min)
            min_samples: Min snapshots needed for detection
            price_cluster_threshold: Price clustering tolerance (1 bps)
            min_confidence: Minimum confidence to return pattern
        """
        self.window_sec = window_sec
        self.min_samples = min_samples
        self.price_threshold = price_cluster_threshold
        self.min_confidence = min_confidence
        
        # Order book history: symbol -> deque of snapshots
        self._snapshots: Dict[str, deque] = {}
        
        # Detected patterns cache
        self._patterns: Dict[str, MMPattern] = {}
        
        # Price level tracking: symbol -> {price: count}
        self._bid_levels: Dict[str, defaultdict] = {}
        self._ask_levels: Dict[str, defaultdict] = {}
        
    async def on_book_update(
        self,
        symbol: str,
        best_bid: float,
        best_ask: float,
        bid_size: float,
        ask_size: float,
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Process order book update
        
        Args:
            symbol: Trading pair
            best_bid: Best bid price
            best_ask: Best ask price
            bid_size: Size at best bid
            ask_size: Size at best ask
            timestamp: Update time (default: now)
        """
        if timestamp is None:
            timestamp = utc_now()
        
        # Calculate mid and spread
        mid_price = (best_bid + best_ask) / 2
        spread_bps = ((best_ask - best_bid) / mid_price) * 10000
        
        # Create snapshot
        snapshot = OrderBookSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            mid_price=mid_price,
            spread_bps=spread_bps
        )
        
        # Store snapshot
        if symbol not in self._snapshots:
            self._snapshots[symbol] = deque(maxlen=1000)
            self._bid_levels[symbol] = defaultdict(int)
            self._ask_levels[symbol] = defaultdict(int)
        
        self._snapshots[symbol].append(snapshot)
        
        # Track price levels
        self._track_price_level(symbol, best_bid, 'bid')
        self._track_price_level(symbol, best_ask, 'ask')
        
        # Clean old snapshots
        self._clean_old_snapshots(symbol)
    
    def _track_price_level(self, symbol: str, price: float, side: str) -> None:
        """Track how often a price level appears (MM detection)"""
        # Round to avoid floating point issues
        price_key = round(price, 8)
        
        if side == 'bid':
            self._bid_levels[symbol][price_key] += 1
        else:
            self._ask_levels[symbol][price_key] += 1
    
    def _clean_old_snapshots(self, symbol: str) -> None:
        """Remove snapshots older than window"""
        if symbol not in self._snapshots:
            return
        
        cutoff = utc_now() - timedelta(seconds=self.window_sec)
        
        while (
            self._snapshots[symbol] and
            self._snapshots[symbol][0].timestamp < cutoff
        ):
            self._snapshots[symbol].popleft()
    
    def detect_pattern(self, symbol: str) -> Optional[MMPattern]:
        """
        Detect market maker pattern
        
        Returns:
            MMPattern if detected with sufficient confidence
            None if insufficient data or low confidence
        """
        snapshots = self._get_snapshots_in_window(symbol)
        
        if len(snapshots) < self.min_samples:
            return None
        
        # Analyze boundaries
        mm_lower, lower_confidence = self._find_mm_boundary(symbol, 'bid')
        mm_upper, upper_confidence = self._find_mm_boundary(symbol, 'ask')
        
        # Analyze order sizes
        avg_bid_size = statistics.mean([s.bid_size for s in snapshots])
        avg_ask_size = statistics.mean([s.ask_size for s in snapshots])
        avg_order_size = (avg_bid_size + avg_ask_size) / 2
        
        # Estimate mid price for USD calculation
        avg_mid = statistics.mean([s.mid_price for s in snapshots])
        avg_order_size_usd = avg_order_size * avg_mid
        
        # Calculate refresh rate
        refresh_rate = self._calculate_refresh_rate(snapshots)
        
        # Calculate average spread
        avg_spread_bps = statistics.mean([s.spread_bps for s in snapshots])
        
        # Calculate overall confidence
        confidence = self._calculate_confidence(
            symbol,
            len(snapshots),
            lower_confidence,
            upper_confidence,
            refresh_rate
        )
        
        if confidence < self.min_confidence:
            return None
        
        # Calculate recommendations
        best_entry = mm_lower if mm_lower else snapshots[-1].best_bid
        best_exit = mm_upper if mm_upper else snapshots[-1].best_ask
        
        # Safe order size: 80% of MM capacity (conservative)
        safe_size_usd = avg_order_size_usd * 0.8
        
        pattern = MMPattern(
            symbol=symbol,
            detected_at=utc_now(),
            mm_lower_bound=mm_lower,
            mm_upper_bound=mm_upper,
            mm_avg_order_size=avg_order_size_usd,
            mm_refresh_rate=refresh_rate,
            mm_spread_bps=avg_spread_bps,
            mm_confidence=confidence,
            samples_count=len(snapshots),
            best_entry_price=best_entry,
            best_exit_price=best_exit,
            safe_order_size_usd=safe_size_usd,
            window_sec=self.window_sec,
            last_updated=utc_now()
        )
        
        # Cache
        self._patterns[symbol] = pattern
        
        return pattern
    
    def _find_mm_boundary(
        self,
        symbol: str,
        side: str
    ) -> Tuple[Optional[float], float]:
        """
        Find MM boundary (most common price level)
        
        Returns:
            (boundary_price, confidence)
        """
        levels = self._bid_levels[symbol] if side == 'bid' else self._ask_levels[symbol]
        
        if not levels:
            return None, 0.0
        
        # Find most common price
        max_count = max(levels.values())
        most_common_price = max(levels, key=levels.get)
        
        # Confidence: ratio of max count to total observations
        total_count = sum(levels.values())
        confidence = max_count / total_count if total_count > 0 else 0.0
        
        return most_common_price, confidence
    
    def _calculate_refresh_rate(self, snapshots: List[OrderBookSnapshot]) -> float:
        """
        Calculate how often MM updates orders (Hz)
        
        Logic: Count price changes divided by time span
        """
        if len(snapshots) < 2:
            return 0.0
        
        # Count bid/ask changes
        changes = 0
        for i in range(1, len(snapshots)):
            if (snapshots[i].best_bid != snapshots[i-1].best_bid or
                snapshots[i].best_ask != snapshots[i-1].best_ask):
                changes += 1
        
        # Time span
        time_span = (snapshots[-1].timestamp - snapshots[0].timestamp).total_seconds()
        
        if time_span == 0:
            return 0.0
        
        # Refresh rate (Hz)
        refresh_rate = changes / time_span
        
        return refresh_rate
    
    def _calculate_confidence(
        self,
        symbol: str,
        sample_count: int,
        lower_confidence: float,
        upper_confidence: float,
        refresh_rate: float
    ) -> float:
        """
        Calculate overall detection confidence
        
        Factors:
        - Sample count (more = better)
        - Boundary stability (higher = better)
        - Refresh rate (consistent = better)
        """
        # Sample confidence (sigmoid curve)
        sample_conf = min(1.0, sample_count / 50.0)
        
        # Boundary confidence (average of both)
        boundary_conf = (lower_confidence + upper_confidence) / 2
        
        # Refresh rate confidence (ideal: 0.5 - 5 Hz)
        if 0.5 <= refresh_rate <= 5.0:
            refresh_conf = 1.0
        elif refresh_rate < 0.5:
            refresh_conf = refresh_rate / 0.5
        else:
            refresh_conf = max(0.0, 1.0 - (refresh_rate - 5.0) / 10.0)
        
        # Weighted average
        confidence = (
            sample_conf * 0.3 +
            boundary_conf * 0.5 +
            refresh_conf * 0.2
        )

        # Phase 2: Tape pressure boost
        if TAPE_AVAILABLE:
            try:
                tape = get_tape_tracker()
                tape_metrics = tape.get_metrics(symbol)
                
                if tape_metrics and tape_metrics.total_trades > 5:
                    # Buy pressure bonus (if aggressive buying detected)
                    if tape_metrics.buy_pressure > 0.65:
                        boost = (tape_metrics.buy_pressure - 0.5) * 0.2  # Max +10%
                        confidence *= (1.0 + boost)
                    
                    # Large trades detected (whale activity)
                    if tape_metrics.large_trades > 0:
                        confidence *= 1.05  # +5% bonus
            except Exception:
                # Tape tracker failed - ignore
                pass
        
        return confidence
    
    def _get_snapshots_in_window(self, symbol: str) -> List[OrderBookSnapshot]:
        """Get snapshots within time window"""
        if symbol not in self._snapshots:
            return []
        
        cutoff = utc_now() - timedelta(seconds=self.window_sec)
        
        snapshots = [
            s for s in self._snapshots[symbol]
            if s.timestamp >= cutoff
        ]
        
        return snapshots
    
    def get_pattern(self, symbol: str) -> Optional[MMPattern]:
        """Get cached pattern or detect new one"""
        # Check cache first
        if symbol in self._patterns:
            pattern = self._patterns[symbol]
            # If recent (< 60s old), return cached
            age = (utc_now() - pattern.last_updated).total_seconds()
            if age < 60:
                return pattern
        
        # Detect new pattern
        return self.detect_pattern(symbol)
    
    def is_mm_detected(self, symbol: str) -> bool:
        """Quick check if MM pattern detected"""
        pattern = self.get_pattern(symbol)
        return pattern is not None and pattern.mm_confidence >= self.min_confidence
    
    def get_safe_order_size(self, symbol: str) -> Optional[float]:
        """Get safe order size (USD) to not scare MM"""
        pattern = self.get_pattern(symbol)
        return pattern.safe_order_size_usd if pattern else None
    
    def is_mm_gone(self, symbol: str, spread_bps: float) -> tuple[bool, str]:
        """Check if MM has left (emergency signal)"""
        if spread_bps > 30:
            return True, f"spread:{spread_bps:.1f}bps"
        
        pattern = self.get_pattern(symbol)
        if not pattern:
            return True, "no_pattern"
        
        if pattern.mm_confidence < 0.5:
            return True, f"conf:{pattern.mm_confidence:.2f}"
        
        if pattern.mm_spread_bps > 0 and spread_bps > pattern.mm_spread_bps * 3:
            return True, f"3x_spread"
    
        return False, "ok"
    
    def get_summary(self, symbol: str) -> dict:
        """Get human-readable summary"""
        pattern = self.get_pattern(symbol)
        
        if not pattern:
            return {
                'symbol': symbol,
                'mm_detected': False,
                'message': 'Insufficient data or low confidence'
            }
        
        return {
            'symbol': symbol,
            'mm_detected': True,
            'confidence': round(pattern.mm_confidence, 3),
            'lower_bound': pattern.mm_lower_bound,
            'upper_bound': pattern.mm_upper_bound,
            'avg_order_size_usd': round(pattern.mm_avg_order_size, 2),
            'refresh_rate_hz': round(pattern.mm_refresh_rate, 2),
            'spread_bps': round(pattern.mm_spread_bps, 2),
            'best_entry': pattern.best_entry_price,
            'best_exit': pattern.best_exit_price,
            'safe_size_usd': round(pattern.safe_order_size_usd, 2),
            'samples': pattern.samples_count,
            'window_sec': pattern.window_sec
        }


# Global instance
_mm_detector: Optional[MMDetector] = None


def get_mm_detector() -> MMDetector:
    """Get global MM detector instance"""
    global _mm_detector
    if _mm_detector is None:
        _mm_detector = MMDetector(
            window_sec=300,  # 5 minutes
            min_samples=20,
            min_confidence=0.7
        )
    return _mm_detector