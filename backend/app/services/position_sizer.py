"""
Adaptive Position Sizer - Calculate optimal order size based on MM capacity

Purpose: Determine safe order size to avoid scaring MM away
- Conservative: 80% of MM capacity
- Balanced: 100% of MM capacity  
- Aggressive: 120% of MM capacity (risky!)

Also calculates if splitting is needed.

Author: Keeper Memory AI - Phase 2
Date: November 13, 2025
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import math

from app.services.mm_detector import get_mm_detector, MMPattern


def utc_now() -> datetime:
    """Get current UTC time with timezone"""
    return datetime.now(timezone.utc)


class SizingMode(Enum):
    """Position sizing mode"""
    CONSERVATIVE = "conservative"  # 80% of MM capacity
    BALANCED = "balanced"          # 100% of MM capacity
    AGGRESSIVE = "aggressive"      # 120% of MM capacity
    FIXED = "fixed"                # Fixed size (ignore MM)


@dataclass
class PositionSize:
    """Calculated position size with reasoning"""
    symbol: str
    
    # Requested
    target_size_usd: float
    
    # Calculated
    safe_size_usd: float           # Safe size per order
    split_needed: bool             # Should split?
    split_count: int               # How many orders
    split_delay_sec: float         # Delay between splits
    
    # MM Info
    mm_detected: bool
    mm_confidence: Optional[float]
    mm_capacity_usd: Optional[float]
    
    # Reasoning
    sizing_mode: SizingMode
    reasoning: str                 # Why this size?
    risk_level: str                # LOW/MEDIUM/HIGH
    
    # Metadata
    calculated_at: datetime


class PositionSizer:
    """
    Adaptive Position Sizer
    
    Calculates optimal order size based on:
    - MM capacity (if detected)
    - Target size
    - Risk appetite
    
    Features:
    - Conservative/Balanced/Aggressive modes
    - Automatic split calculation
    - Risk assessment
    - Explainable reasoning
    """
    
    def __init__(
        self,
        default_size_usd: float = 2.0,
        min_size_usd: float = 1.0,
        max_size_usd: float = 10.0,
        max_split_count: int = 10,
        min_split_delay_sec: float = 1.0,
        max_split_delay_sec: float = 5.0
    ):
        """
        Args:
            default_size_usd: Default order size if no MM detected
            min_size_usd: Minimum order size
            max_size_usd: Maximum order size
            max_split_count: Max number of splits allowed
            min_split_delay_sec: Min delay between split orders
            max_split_delay_sec: Max delay between split orders
        """
        self.default_size = default_size_usd
        self.min_size = min_size_usd
        self.max_size = max_size_usd
        self.max_split_count = max_split_count
        self.min_split_delay = min_split_delay_sec
        self.max_split_delay = max_split_delay_sec
        
        # MM detector instance
        self.mm_detector = get_mm_detector()
    
    def calculate_size(
        self,
        symbol: str,
        target_size_usd: float,
        mode: SizingMode = SizingMode.CONSERVATIVE
    ) -> PositionSize:
        """
        Calculate optimal position size
        
        Args:
            symbol: Trading pair
            target_size_usd: Desired total size
            mode: Sizing mode (conservative/balanced/aggressive)
            
        Returns:
            PositionSize with all calculations
        """
        # Get MM pattern
        mm_pattern = self.mm_detector.get_pattern(symbol)
        
        if mm_pattern and mm_pattern.mm_confidence >= 0.7:
            # MM detected - use MM-based sizing
            return self._calculate_mm_based_size(
                symbol,
                target_size_usd,
                mm_pattern,
                mode
            )
        else:
            # No MM - use fixed sizing
            return self._calculate_fixed_size(
                symbol,
                target_size_usd,
                mode
            )
    
    def _calculate_mm_based_size(
        self,
        symbol: str,
        target_size_usd: float,
        mm_pattern: MMPattern,
        mode: SizingMode
    ) -> PositionSize:
        """Calculate size based on MM capacity"""
        
        # Get multiplier based on mode
        if mode == SizingMode.CONSERVATIVE:
            multiplier = 0.8  # 80% of MM capacity
            risk_level = "LOW"
        elif mode == SizingMode.BALANCED:
            multiplier = 1.0  # 100% of MM capacity
            risk_level = "MEDIUM"
        elif mode == SizingMode.AGGRESSIVE:
            multiplier = 1.2  # 120% of MM capacity (risky!)
            risk_level = "HIGH"
        else:
            # Fallback to conservative
            multiplier = 0.8
            risk_level = "LOW"
        
        # Calculate safe size per order
        mm_capacity = mm_pattern.mm_avg_order_size
        safe_size_per_order = mm_capacity * multiplier
        
        # Clamp to limits
        safe_size_per_order = max(self.min_size, min(self.max_size, safe_size_per_order))
        
        # Check if split needed
        split_needed = target_size_usd > safe_size_per_order
        
        if split_needed:
            # Calculate split count
            split_count = math.ceil(target_size_usd / safe_size_per_order)
            split_count = min(split_count, self.max_split_count)
            
            # Calculate delay based on MM refresh rate
            # Slower MM = longer delay
            if mm_pattern.mm_refresh_rate > 0:
                # Wait ~2x the refresh period
                split_delay = (2.0 / mm_pattern.mm_refresh_rate)
                split_delay = max(self.min_split_delay, min(self.max_split_delay, split_delay))
            else:
                split_delay = self.min_split_delay
            
            reasoning = (
                f"Split into {split_count} orders to match MM capacity "
                f"(${mm_capacity:.2f}). Using {mode.value} mode ({multiplier*100:.0f}%). "
                f"Confidence: {mm_pattern.mm_confidence:.1%}"
            )
        else:
            split_count = 1
            split_delay = 0.0
            reasoning = (
                f"Single order safe. MM capacity: ${mm_capacity:.2f}, "
                f"target: ${target_size_usd:.2f}. Using {mode.value} mode. "
                f"Confidence: {mm_pattern.mm_confidence:.1%}"
            )
        
        return PositionSize(
            symbol=symbol,
            target_size_usd=target_size_usd,
            safe_size_usd=safe_size_per_order,
            split_needed=split_needed,
            split_count=split_count,
            split_delay_sec=split_delay,
            mm_detected=True,
            mm_confidence=mm_pattern.mm_confidence,
            mm_capacity_usd=mm_capacity,
            sizing_mode=mode,
            reasoning=reasoning,
            risk_level=risk_level,
            calculated_at=utc_now()
        )
    
    def _calculate_fixed_size(
        self,
        symbol: str,
        target_size_usd: float,
        mode: SizingMode
    ) -> PositionSize:
        """Calculate fixed size (no MM detected)"""
        
        # Use default size
        safe_size_per_order = self.default_size
        
        # Clamp to limits
        safe_size_per_order = max(self.min_size, min(self.max_size, safe_size_per_order))
        
        # Check if split needed
        split_needed = target_size_usd > safe_size_per_order
        
        if split_needed:
            split_count = math.ceil(target_size_usd / safe_size_per_order)
            split_count = min(split_count, self.max_split_count)
            split_delay = 2.0  # Default delay
            
            reasoning = (
                f"No MM detected. Using default size ${self.default_size:.2f}. "
                f"Split into {split_count} orders."
            )
        else:
            split_count = 1
            split_delay = 0.0
            reasoning = f"No MM detected. Using default size ${self.default_size:.2f}."
        
        return PositionSize(
            symbol=symbol,
            target_size_usd=target_size_usd,
            safe_size_usd=safe_size_per_order,
            split_needed=split_needed,
            split_count=split_count,
            split_delay_sec=split_delay,
            mm_detected=False,
            mm_confidence=None,
            mm_capacity_usd=None,
            sizing_mode=mode,
            reasoning=reasoning,
            risk_level="MEDIUM",
            calculated_at=utc_now()
        )
    
    def get_summary(self, position_size: PositionSize) -> dict:
        """Get human-readable summary"""
        return {
            'symbol': position_size.symbol,
            'target_size_usd': round(position_size.target_size_usd, 2),
            'safe_size_usd': round(position_size.safe_size_usd, 2),
            'split_needed': position_size.split_needed,
            'split_count': position_size.split_count,
            'split_delay_sec': round(position_size.split_delay_sec, 1),
            'mm_detected': position_size.mm_detected,
            'mm_confidence': round(position_size.mm_confidence, 3) if position_size.mm_confidence else None,
            'mm_capacity_usd': round(position_size.mm_capacity_usd, 2) if position_size.mm_capacity_usd else None,
            'mode': position_size.sizing_mode.value,
            'risk_level': position_size.risk_level,
            'reasoning': position_size.reasoning
        }


# Global instance
_position_sizer: Optional[PositionSizer] = None


def get_position_sizer() -> PositionSizer:
    """Get global position sizer instance"""
    global _position_sizer
    if _position_sizer is None:
        _position_sizer = PositionSizer(
            default_size_usd=2.0,
            min_size_usd=1.0,
            max_size_usd=10.0
        )
    return _position_sizer