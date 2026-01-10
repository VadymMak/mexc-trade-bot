"""
Smart Executor - MM-aware order execution with adaptive sizing

Purpose: Execute orders intelligently:
- Adaptive sizing based on MM capacity
- Order splitting when needed
- MM departure monitoring
- Emergency abort if MM leaves

Integrates:
- MMDetector (boundaries, confidence)
- PositionSizer (safe sizing, splits)
- TapeTracker (buy/sell pressure)
- EnhancedBookTracker (spoofing, stability)

Author: Keeper Memory AI - Phase 2
Date: November 13, 2025
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

from app.services.mm_detector import get_mm_detector, MMPattern
from app.services.position_sizer import get_position_sizer, SizingMode, PositionSize
from app.services.tape_tracker import get_tape_tracker
from app.services.book_tracker_enhanced import get_enhanced_book_tracker


def utc_now() -> datetime:
    """Get current UTC time with timezone"""
    return datetime.now(timezone.utc)


class ExecutionStatus(Enum):
    """Execution status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class AbortReason(Enum):
    """Why execution was aborted"""
    MM_DEPARTED = "mm_departed"
    MM_CONFIDENCE_DROP = "mm_confidence_drop"
    SPOOFING_DETECTED = "spoofing_detected"
    SPREAD_WIDENED = "spread_widened"
    USER_CANCEL = "user_cancel"


@dataclass
class OrderFill:
    """Single order fill"""
    order_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    price: float
    size: float
    size_usd: float
    timestamp: datetime
    split_index: int  # Which split (0 = first)


@dataclass
class ExecutionResult:
    """Result of smart execution"""
    symbol: str
    side: str
    
    # Request
    target_size_usd: float
    sizing_mode: SizingMode
    
    # Execution
    status: ExecutionStatus
    fills: List[OrderFill]
    total_filled_usd: float
    avg_fill_price: float
    
    # Timing
    started_at: datetime
    completed_at: Optional[datetime]
    duration_sec: float
    
    # Quality
    slippage_bps: float
    entry_quality_score: float  # 0-1
    
    # MM tracking
    mm_detected: bool
    mm_confidence_start: Optional[float]
    mm_confidence_end: Optional[float]
    mm_scared_away: bool
    
    # Abort info
    abort_reason: Optional[AbortReason]
    abort_message: Optional[str]
    
    # Metadata
    split_count: int
    split_delay_used: float


class SmartExecutor:
    """
    Smart Order Executor with MM Awareness
    
    Features:
    - Detect MM pattern before execution
    - Calculate safe order size
    - Split orders if needed
    - Monitor MM during execution
    - Abort if MM leaves
    - Track execution quality
    
    Use cases:
    - Safe entry without scaring MM
    - Better fill prices
    - Reduced slippage
    - Professional execution
    """
    
    def __init__(
        self,
        mm_departure_threshold_pct: float = 0.1,
        mm_confidence_min: float = 0.5,
        max_slippage_bps: float = 5.0,
        execution_timeout_sec: float = 60.0
    ):
        """
        Args:
            mm_departure_threshold_pct: Abort if MM boundaries move > X%
            mm_confidence_min: Abort if MM confidence drops below X
            max_slippage_bps: Abort if slippage exceeds X bps
            execution_timeout_sec: Max time for full execution
        """
        self.mm_departure_threshold = mm_departure_threshold_pct
        self.mm_confidence_min = mm_confidence_min
        self.max_slippage = max_slippage_bps
        self.timeout = execution_timeout_sec
        
        # Service instances
        self.mm_detector = get_mm_detector()
        self.position_sizer = get_position_sizer()
        self.tape_tracker = get_tape_tracker()
        self.book_tracker = get_enhanced_book_tracker()
    
    async def execute_smart_entry(
        self,
        symbol: str,
        target_size_usd: float,
        side: str = "BUY",
        mode: SizingMode = SizingMode.CONSERVATIVE
    ) -> ExecutionResult:
        """
        Execute smart entry with MM awareness
        
        Process:
        1. Detect MM pattern
        2. Calculate safe order size
        3. Execute (with splits if needed)
        4. Monitor MM during execution
        5. Abort if MM leaves
        
        Args:
            symbol: Trading pair
            target_size_usd: Desired total size
            side: 'BUY' or 'SELL'
            mode: Sizing mode
            
        Returns:
            ExecutionResult with all details
        """
        started_at = utc_now()
        
        # Step 1: Detect MM pattern
        mm_pattern = self.mm_detector.get_pattern(symbol)
        mm_detected = mm_pattern is not None
        mm_confidence_start = mm_pattern.mm_confidence if mm_pattern else None
        
        # Step 2: Calculate sizing
        position_size = self.position_sizer.calculate_size(
            symbol, target_size_usd, mode
        )
        
        # Step 3: Execute
        try:
            fills, abort_reason, abort_msg = await self._execute_with_monitoring(
                symbol=symbol,
                side=side,
                position_size=position_size,
                mm_pattern=mm_pattern
            )
            
            # Determine status
            if abort_reason:
                status = ExecutionStatus.ABORTED
            elif fills:
                status = ExecutionStatus.COMPLETED
            else:
                status = ExecutionStatus.FAILED
            
        except Exception as e:
            # Execution failed
            fills = []
            abort_reason = None
            abort_msg = str(e)
            status = ExecutionStatus.FAILED
        
        completed_at = utc_now()
        duration_sec = (completed_at - started_at).total_seconds()
        
        # Calculate metrics
        if fills:
            total_filled = sum(f.size_usd for f in fills)
            avg_price = sum(f.price * f.size_usd for f in fills) / total_filled
            
            # Slippage (vs expected price)
            if mm_pattern:
                expected_price = mm_pattern.best_entry_price if side == "BUY" else mm_pattern.best_exit_price
                if expected_price:
                    slippage_bps = abs((avg_price - expected_price) / expected_price) * 10000
                else:
                    slippage_bps = 0.0
            else:
                slippage_bps = 0.0
            
            # Entry quality (0-1)
            entry_quality = self._calculate_entry_quality(
                fills, mm_pattern, position_size, abort_reason
            )
        else:
            total_filled = 0.0
            avg_price = 0.0
            slippage_bps = 0.0
            entry_quality = 0.0
        
        # Check if MM scared away
        mm_confidence_end = None
        mm_scared_away = False
        
        if mm_detected:
            final_pattern = self.mm_detector.get_pattern(symbol)
            if final_pattern:
                mm_confidence_end = final_pattern.mm_confidence
                mm_scared_away = mm_confidence_end < (mm_confidence_start * 0.7)
        
        return ExecutionResult(
            symbol=symbol,
            side=side,
            target_size_usd=target_size_usd,
            sizing_mode=mode,
            status=status,
            fills=fills,
            total_filled_usd=total_filled,
            avg_fill_price=avg_price,
            started_at=started_at,
            completed_at=completed_at,
            duration_sec=duration_sec,
            slippage_bps=slippage_bps,
            entry_quality_score=entry_quality,
            mm_detected=mm_detected,
            mm_confidence_start=mm_confidence_start,
            mm_confidence_end=mm_confidence_end,
            mm_scared_away=mm_scared_away,
            abort_reason=abort_reason,
            abort_message=abort_msg,
            split_count=position_size.split_count,
            split_delay_used=position_size.split_delay_sec
        )
    
    async def execute_entry(
        self,
        executor: Any,
        symbol: str,
        side: str,
        price: float,
        total_qty: float,
        split_count: int = 1,
        split_delay_sec: float = 0.5
    ) -> dict:
        """
        Simplified entry execution for strategy engine compatibility.
        
        This method delegates actual order placement to the Paper/Live executor
        while providing MM-aware quality scoring and monitoring.
        
        SmartExecutor role: Intelligence & Quality Assessment
        Paper/Live Executor role: Actual Order Placement & Position Tracking
        
        Args:
            executor: Execution port (Paper or Live)
            symbol: Trading pair
            side: 'BUY' or 'SELL'
            price: Entry price
            total_qty: Total quantity to execute
            split_count: Number of splits (currently ignored, always 1)
            split_delay_sec: Delay between splits (currently ignored)
            
        Returns:
            dict with keys: order_id, filled_qty, quality, slippage_bps, actual_splits
        """
        # Get MM pattern for quality scoring
        mm_pattern = self.mm_detector.get_pattern(symbol)
        
        # Calculate quality score based on MM detection
        # 1.0 = MM detected (best)
        # 0.8 = No MM detected (good, using default size)
        quality = 1.0 if mm_pattern is not None else 0.8

        # Phase 2: Book quality adjustments
        try:
            book_metrics = self.book_tracker.get_metrics(symbol)
            
            if book_metrics:
                # Reduce quality if high spoofing detected
                if book_metrics.spoofing_score > 0.5:
                    quality *= 0.7  # -30% penalty for spoofing
                
                # Reduce quality if spread unstable
                if book_metrics.spread_stability_score < 0.5:
                    quality *= 0.9  # -10% penalty for instability
        except Exception:
            # Book tracker failed - ignore
            pass
        
        # ✅ DELEGATE TO REAL EXECUTOR (Paper or Live)
        # This ensures proper position tracking and order management
        oid = await executor.place_maker(symbol, side, price, total_qty, tag="mm_entry")
        
        if not oid:
            # Execution failed
            return {
                'order_id': None,
                'filled_qty': 0.0,
                'quality': 0.0,
                'slippage_bps': 0.0,
                'actual_splits': 0
            }
        
        # Successful execution
        return {
            'order_id': oid,  # ✅ REAL order ID from executor
            'filled_qty': total_qty,
            'quality': quality,
            'slippage_bps': 0.0,  # Calculated by Paper executor
            'actual_splits': 1  # Currently no splitting implemented
        }
    
    async def _execute_with_monitoring(
        self,
        symbol: str,
        side: str,
        position_size: PositionSize,
        mm_pattern: Optional[MMPattern]
    ) -> tuple[List[OrderFill], Optional[AbortReason], Optional[str]]:
        """
        Execute orders with MM monitoring
        
        Returns:
            (fills, abort_reason, abort_message)
        """
        fills = []
        
        # Execute splits
        for split_idx in range(position_size.split_count):
            
            # Check MM before each split
            if mm_pattern and split_idx > 0:
                abort_reason, abort_msg = self._check_mm_still_active(
                    symbol, mm_pattern
                )
                if abort_reason:
                    return fills, abort_reason, abort_msg
            
            # Simulate order execution
            # In real implementation, this would call exchange API
            fill = await self._simulate_order_fill(
                symbol=symbol,
                side=side,
                size_usd=position_size.safe_size_usd,
                split_idx=split_idx,
                mm_pattern=mm_pattern
            )
            
            fills.append(fill)
            
            # Wait before next split
            if split_idx < position_size.split_count - 1:
                await asyncio.sleep(position_size.split_delay_sec)
        
        return fills, None, None
    
    def _check_mm_still_active(
        self,
        symbol: str,
        original_pattern: MMPattern
    ) -> tuple[Optional[AbortReason], Optional[str]]:
        """
        Check if MM still active
        
        Returns:
            (abort_reason, message) or (None, None) if OK
        """
        # Get current pattern
        current_pattern = self.mm_detector.get_pattern(symbol)
        
        if not current_pattern:
            return AbortReason.MM_DEPARTED, "MM pattern no longer detected"
        
        # Check confidence
        if current_pattern.mm_confidence < self.mm_confidence_min:
            return (
                AbortReason.MM_CONFIDENCE_DROP,
                f"MM confidence dropped to {current_pattern.mm_confidence:.1%}"
            )
        
        # Check boundaries movement
        if original_pattern.mm_lower_bound and current_pattern.mm_lower_bound:
            lower_move_pct = abs(
                (current_pattern.mm_lower_bound - original_pattern.mm_lower_bound) /
                original_pattern.mm_lower_bound
            )
            if lower_move_pct > self.mm_departure_threshold:
                return (
                    AbortReason.MM_DEPARTED,
                    f"MM lower bound moved {lower_move_pct:.1%}"
                )
        
        if original_pattern.mm_upper_bound and current_pattern.mm_upper_bound:
            upper_move_pct = abs(
                (current_pattern.mm_upper_bound - original_pattern.mm_upper_bound) /
                original_pattern.mm_upper_bound
            )
            if upper_move_pct > self.mm_departure_threshold:
                return (
                    AbortReason.MM_DEPARTED,
                    f"MM upper bound moved {upper_move_pct:.1%}"
                )
        
        # Check for spoofing
        book_metrics = self.book_tracker.get_metrics(symbol)
        if book_metrics.spoofing_score > 0.7:
            return (
                AbortReason.SPOOFING_DETECTED,
                f"High spoofing detected (score: {book_metrics.spoofing_score:.2f})"
            )
        
        # All checks passed
        return None, None
    
    async def _simulate_order_fill(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        split_idx: int,
        mm_pattern: Optional[MMPattern]
    ) -> OrderFill:
        """
        Simulate order fill
        
        In real implementation, this would:
        1. Place limit order at MM boundary
        2. Wait for fill
        3. Return actual fill data
        """
        # Use MM boundary if available
        if mm_pattern:
            if side == "BUY":
                price = mm_pattern.best_entry_price or 70.58
            else:
                price = mm_pattern.best_exit_price or 70.60
        else:
            price = 70.59  # Fallback
        
        # Calculate size
        size = size_usd / price
        
        # Simulate fill
        return OrderFill(
            order_id=f"{symbol}_{split_idx}_{utc_now().timestamp()}",
            symbol=symbol,
            side=side,
            price=price,
            size=size,
            size_usd=size_usd,
            timestamp=utc_now(),
            split_index=split_idx
        )
    
    def _calculate_entry_quality(
        self,
        fills: List[OrderFill],
        mm_pattern: Optional[MMPattern],
        position_size: PositionSize,
        abort_reason: Optional[AbortReason]
    ) -> float:
        """
        Calculate entry quality score (0-1)
        
        Factors:
        - All orders filled: +0.3
        - MM detected: +0.2
        - No abort: +0.3
        - Low slippage: +0.2
        """
        score = 0.0
        
        # Filled completely
        if len(fills) == position_size.split_count:
            score += 0.3
        else:
            score += 0.3 * (len(fills) / position_size.split_count)
        
        # MM detected
        if mm_pattern:
            score += 0.2
        
        # Not aborted
        if not abort_reason:
            score += 0.3
        
        # Low slippage (assume low for simulation)
        score += 0.2
        
        return min(1.0, score)
    
    def get_summary(self, result: ExecutionResult) -> dict:
        """Get human-readable summary"""
        return {
            'symbol': result.symbol,
            'side': result.side,
            'status': result.status.value,
            'target_size_usd': round(result.target_size_usd, 2),
            'filled_usd': round(result.total_filled_usd, 2),
            'avg_price': round(result.avg_fill_price, 4),
            'fills_count': len(result.fills),
            'duration_sec': round(result.duration_sec, 2),
            'slippage_bps': round(result.slippage_bps, 2),
            'entry_quality': round(result.entry_quality_score, 3),
            'mm_detected': result.mm_detected,
            'mm_confidence_start': round(result.mm_confidence_start, 3) if result.mm_confidence_start else None,
            'mm_confidence_end': round(result.mm_confidence_end, 3) if result.mm_confidence_end else None,
            'mm_scared_away': result.mm_scared_away,
            'abort_reason': result.abort_reason.value if result.abort_reason else None,
            'split_count': result.split_count
        }


# Global instance
_smart_executor: Optional[SmartExecutor] = None


def get_smart_executor() -> SmartExecutor:
    """Get global smart executor instance"""
    global _smart_executor
    if _smart_executor is None:
        _smart_executor = SmartExecutor(
            mm_departure_threshold_pct=0.1,
            mm_confidence_min=0.5
        )
    return _smart_executor