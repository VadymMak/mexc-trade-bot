# app/services/position_slot_manager.py
"""
Position Slot Manager for High Frequency Trading

Manages multiple concurrent positions per symbol using a slot-based system.
Each symbol can have 5-10 active position slots that rotate independently.

Key Features:
- Multiple concurrent positions per symbol (5-10 slots)
- Independent lifecycle per slot
- Fast slot availability lookup
- FIFO rotation (oldest slots close first)
- No cooldowns between slot reuses
- High frequency support (1,000+ trades/day per symbol)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Set
from datetime import datetime, timezone
import asyncio


@dataclass
class PositionSlot:
    """
    Represents a single position slot for a symbol.
    
    A slot can be either OPEN (has active position) or AVAILABLE (ready for new position).
    """
    slot_id: int                    # Slot number (0-9)
    symbol: str                     # Trading symbol
    status: str = "AVAILABLE"       # AVAILABLE | OPEN | CLOSING
    
    # Position data (when OPEN)
    entry_price: Optional[Decimal] = None
    qty: Optional[Decimal] = None
    entry_time: Optional[datetime] = None
    entry_time_ms: Optional[int] = None
    client_order_id: Optional[str] = None
    
    # Performance tracking
    trades_in_slot: int = 0         # Total trades completed in this slot today
    last_exit_time_ms: Optional[int] = None
    
    # Risk tracking
    pnl_usd: Decimal = Decimal("0")
    win_count: int = 0
    loss_count: int = 0


@dataclass
class SymbolSlots:
    """
    Manages all position slots for a single symbol.
    """
    symbol: str
    max_slots: int = 8              # Default: 8 concurrent positions
    slots: List[PositionSlot] = field(default_factory=list)
    
    # Performance metrics
    total_trades_today: int = 0
    total_pnl_today: Decimal = Decimal("0")
    
    def __post_init__(self):
        if not self.slots:
            self.slots = [
                PositionSlot(slot_id=i, symbol=self.symbol)
                for i in range(self.max_slots)
            ]
    
    def get_available_slot(self) -> Optional[PositionSlot]:
        """Get first available slot, or None if all full."""
        for slot in self.slots:
            if slot.status == "AVAILABLE":
                return slot
        return None
    
    def get_open_slots(self) -> List[PositionSlot]:
        """Get all slots with open positions."""
        return [s for s in self.slots if s.status == "OPEN"]
    
    def get_slot_utilization(self) -> float:
        """Get percentage of slots currently in use."""
        open_count = len(self.get_open_slots())
        return (open_count / self.max_slots) * 100 if self.max_slots > 0 else 0.0
    
    def get_slot_by_id(self, slot_id: int) -> Optional[PositionSlot]:
        """Get specific slot by ID."""
        if 0 <= slot_id < len(self.slots):
            return self.slots[slot_id]
        return None


class PositionSlotManager:
    """
    High-level manager for all position slots across all symbols.
    
    Responsibilities:
    - Create and track position slots
    - Provide fast slot availability checks
    - Track performance per slot and per symbol
    - Support high frequency rotation (1,000+ trades/day per symbol)
    """
    
    def __init__(self, max_slots_per_symbol: int = 8):
        self.max_slots_per_symbol = max_slots_per_symbol
        self._symbol_slots: Dict[str, SymbolSlots] = {}
        self._lock = asyncio.Lock()
        
        # Global tracking
        self._total_trades_today = 0
        self._start_time_ms = self._now_ms()
    
    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
    
    # ===== Slot Management =====
    
    async def initialize_symbol(self, symbol: str, max_slots: Optional[int] = None) -> None:
        """
        Initialize position slots for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            max_slots: Number of concurrent positions (default: 8)
        """
        async with self._lock:
            if symbol not in self._symbol_slots:
                slots_count = max_slots or self.max_slots_per_symbol
                self._symbol_slots[symbol] = SymbolSlots(
                    symbol=symbol,
                    max_slots=slots_count
                )
                print(f"[SLOTS] Initialized {symbol}: {slots_count} slots")
    
    async def get_available_slot(self, symbol: str) -> Optional[PositionSlot]:
        """
        Get first available slot for a symbol.
        
        Returns:
            PositionSlot if available, None if all slots full
        """
        async with self._lock:
            if symbol not in self._symbol_slots:
                await self.initialize_symbol(symbol)
            
            return self._symbol_slots[symbol].get_available_slot()
    
    async def open_slot(
        self,
        symbol: str,
        slot_id: int,
        entry_price: Decimal,
        qty: Decimal,
        client_order_id: str
    ) -> bool:
        """
        Mark a slot as OPEN with position data.
        
        Returns:
            True if successful, False if slot not available
        """
        async with self._lock:
            if symbol not in self._symbol_slots:
                return False
            
            slot = self._symbol_slots[symbol].get_slot_by_id(slot_id)
            if not slot or slot.status != "AVAILABLE":
                return False
            
            # Open the slot
            now = datetime.now(timezone.utc)
            slot.status = "OPEN"
            slot.entry_price = entry_price
            slot.qty = qty
            slot.entry_time = now
            slot.entry_time_ms = self._now_ms()
            slot.client_order_id = client_order_id
            
            print(f"[SLOT:{symbol}:{slot_id}] OPENED qty={qty} @ {entry_price}")
            return True
    
    async def close_slot(
        self,
        symbol: str,
        slot_id: int,
        exit_reason: str,
        pnl_usd: Decimal,
        is_win: bool
    ) -> bool:
        """
        Close a slot and mark as available for reuse.
        
        Args:
            symbol: Trading symbol
            slot_id: Slot number
            exit_reason: "TP" | "SL" | "TIMEOUT"
            pnl_usd: P&L in USD
            is_win: True if profitable trade
        
        Returns:
            True if successful
        """
        async with self._lock:
            if symbol not in self._symbol_slots:
                return False
            
            slot = self._symbol_slots[symbol].get_slot_by_id(slot_id)
            if not slot or slot.status != "OPEN":
                return False
            
            # Update slot stats
            slot.trades_in_slot += 1
            slot.pnl_usd += pnl_usd
            if is_win:
                slot.win_count += 1
            else:
                slot.loss_count += 1
            slot.last_exit_time_ms = self._now_ms()
            
            # Update symbol stats
            symbol_slots = self._symbol_slots[symbol]
            symbol_slots.total_trades_today += 1
            symbol_slots.total_pnl_today += pnl_usd
            
            # Update global stats
            self._total_trades_today += 1
            
            # Mark as available for immediate reuse
            slot.status = "AVAILABLE"
            slot.entry_price = None
            slot.qty = None
            slot.entry_time = None
            slot.entry_time_ms = None
            slot.client_order_id = None
            
            print(f"[SLOT:{symbol}:{slot_id}] CLOSED [{exit_reason}] pnl=${pnl_usd:.4f} "
                  f"(total in slot: {slot.trades_in_slot} trades)")
            
            return True
    
    # ===== Query Methods =====
    
    async def get_open_positions(self, symbol: str) -> List[PositionSlot]:
        """Get all open position slots for a symbol."""
        async with self._lock:
            if symbol not in self._symbol_slots:
                return []
            return self._symbol_slots[symbol].get_open_slots()
    
    async def get_all_open_positions(self) -> Dict[str, List[PositionSlot]]:
        """Get all open positions across all symbols."""
        async with self._lock:
            result = {}
            for symbol, slots in self._symbol_slots.items():
                open_slots = slots.get_open_slots()
                if open_slots:
                    result[symbol] = open_slots
            return result
    
    async def get_slot_utilization(self, symbol: str) -> float:
        """Get percentage of slots in use for a symbol."""
        async with self._lock:
            if symbol not in self._symbol_slots:
                return 0.0
            return self._symbol_slots[symbol].get_slot_utilization()
    
    async def get_total_open_positions(self) -> int:
        """Get total number of open positions across all symbols."""
        async with self._lock:
            total = 0
            for slots in self._symbol_slots.values():
                total += len(slots.get_open_slots())
            return total
    
    # ===== Performance Metrics =====
    
    async def get_symbol_stats(self, symbol: str) -> Dict:
        """Get trading statistics for a symbol."""
        async with self._lock:
            if symbol not in self._symbol_slots:
                return {}
            
            slots = self._symbol_slots[symbol]
            open_slots = slots.get_open_slots()
            
            return {
                "symbol": symbol,
                "max_slots": slots.max_slots,
                "open_slots": len(open_slots),
                "available_slots": slots.max_slots - len(open_slots),
                "slot_utilization_pct": slots.get_slot_utilization(),
                "trades_today": slots.total_trades_today,
                "pnl_today": float(slots.total_pnl_today),
            }
    
    async def get_global_stats(self) -> Dict:
        """Get global trading statistics."""
        async with self._lock:
            total_open = 0
            total_available = 0
            symbols_active = 0
            
            for slots in self._symbol_slots.values():
                open_count = len(slots.get_open_slots())
                total_open += open_count
                total_available += (slots.max_slots - open_count)
                if open_count > 0:
                    symbols_active += 1
            
            uptime_hours = (self._now_ms() - self._start_time_ms) / (1000 * 3600)
            trades_per_hour = self._total_trades_today / uptime_hours if uptime_hours > 0 else 0
            
            return {
                "total_symbols": len(self._symbol_slots),
                "symbols_active": symbols_active,
                "total_open_positions": total_open,
                "total_available_slots": total_available,
                "total_trades_today": self._total_trades_today,
                "trades_per_hour": round(trades_per_hour, 1),
                "uptime_hours": round(uptime_hours, 2),
            }
    
    # ===== Maintenance =====
    
    async def reset_daily_stats(self) -> None:
        """Reset daily statistics (call at midnight UTC)."""
        async with self._lock:
            for slots in self._symbol_slots.values():
                slots.total_trades_today = 0
                slots.total_pnl_today = Decimal("0")
                
                for slot in slots.slots:
                    slot.trades_in_slot = 0
                    slot.pnl_usd = Decimal("0")
                    slot.win_count = 0
                    slot.loss_count = 0
            
            self._total_trades_today = 0
            self._start_time_ms = self._now_ms()
            
            print("[SLOTS] Daily stats reset")


# ===== Global Singleton =====

_slot_manager: Optional[PositionSlotManager] = None


def get_slot_manager(max_slots_per_symbol: int = 8) -> PositionSlotManager:
    """Get or create global PositionSlotManager instance."""
    global _slot_manager
    if _slot_manager is None:
        _slot_manager = PositionSlotManager(max_slots_per_symbol)
    return _slot_manager


# ===== Usage Example =====

if __name__ == "__main__":
    async def demo():
        manager = get_slot_manager(max_slots_per_symbol=5)
        
        # Initialize symbol
        await manager.initialize_symbol("BTCUSDT", max_slots=5)
        
        # Check available slot
        slot = await manager.get_available_slot("BTCUSDT")
        print(f"Available slot: {slot.slot_id if slot else 'None'}")
        
        # Open position in slot
        if slot:
            await manager.open_slot(
                symbol="BTCUSDT",
                slot_id=slot.slot_id,
                entry_price=Decimal("50000"),
                qty=Decimal("0.001"),
                client_order_id="test123"
            )
        
        # Check stats
        stats = await manager.get_symbol_stats("BTCUSDT")
        print(f"Symbol stats: {stats}")
        
        # Close position
        if slot:
            await manager.close_slot(
                symbol="BTCUSDT",
                slot_id=slot.slot_id,
                exit_reason="TP",
                pnl_usd=Decimal("1.50"),
                is_win=True
            )
        
        # Check stats again
        stats = await manager.get_symbol_stats("BTCUSDT")
        print(f"Symbol stats after close: {stats}")
    
    asyncio.run(demo())