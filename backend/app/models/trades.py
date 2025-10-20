"""
SQLAlchemy model for trades table.

Stores detailed information about every trade (entry + exit) for analysis and reporting.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func

from app.models.base import Base


class Trade(Base):
    """
    Trade model - represents a complete trade cycle (entry → exit).
    
    Each row represents one complete trade with entry and exit information,
    P&L calculation, fees, and market conditions.
    """
    
    __tablename__ = "trades"
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Trade identification
    trade_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    exchange = Column(String, default="MEXC")
    
    # Entry details
    entry_time = Column(DateTime, nullable=False, index=True)
    entry_price = Column(Float, nullable=False)
    entry_qty = Column(Float, nullable=False)
    entry_side = Column(String, default="BUY")  # BUY or SELL
    
    # Exit details (NULL if position still open)
    exit_time = Column(DateTime, nullable=True, index=True)
    exit_price = Column(Float, nullable=True)
    exit_qty = Column(Float, nullable=True)
    exit_side = Column(String, nullable=True)  # SELL or BUY
    exit_reason = Column(String, nullable=True)  # TP, SL, TIMEOUT, MANUAL
    
    # Performance metrics
    pnl_usd = Column(Float, default=0.0)
    pnl_bps = Column(Float, default=0.0)
    pnl_percent = Column(Float, default=0.0)
    
    # Fees
    entry_fee = Column(Float, default=0.0)
    exit_fee = Column(Float, default=0.0)
    total_fee = Column(Float, default=0.0)
    
    # Timing
    hold_duration_sec = Column(Float, nullable=True)
    
    # Market conditions at entry
    spread_bps_entry = Column(Float, nullable=True)
    imbalance_entry = Column(Float, nullable=True)
    depth_5bps_entry = Column(Float, nullable=True)
    
    # Strategy metadata
    strategy_tag = Column(String, nullable=True)
    strategy_params = Column(Text, nullable=True)  # JSON string
    
    # Status
    status = Column(String, default="CLOSED")  # OPEN, CLOSED, CANCELLED
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self) -> str:
        """String representation."""
        status_str = f"{self.status}"
        pnl_str = f"{self.pnl_usd:+.2f}" if self.pnl_usd else "N/A"
        return (
            f"<Trade(id={self.id}, trade_id='{self.trade_id}', "
            f"symbol='{self.symbol}', status='{status_str}', "
            f"pnl=${pnl_str})>"
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "exchange": self.exchange,
            
            # Entry
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": float(self.entry_price) if self.entry_price else 0.0,
            "entry_qty": float(self.entry_qty) if self.entry_qty else 0.0,
            "entry_side": self.entry_side,
            
            # Exit
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": float(self.exit_price) if self.exit_price else None,
            "exit_qty": float(self.exit_qty) if self.exit_qty else None,
            "exit_side": self.exit_side,
            "exit_reason": self.exit_reason,
            
            # Performance
            "pnl_usd": float(self.pnl_usd) if self.pnl_usd else 0.0,
            "pnl_bps": float(self.pnl_bps) if self.pnl_bps else 0.0,
            "pnl_percent": float(self.pnl_percent) if self.pnl_percent else 0.0,
            
            # Fees
            "entry_fee": float(self.entry_fee) if self.entry_fee else 0.0,
            "exit_fee": float(self.exit_fee) if self.exit_fee else 0.0,
            "total_fee": float(self.total_fee) if self.total_fee else 0.0,
            
            # Timing
            "hold_duration_sec": float(self.hold_duration_sec) if self.hold_duration_sec else None,
            
            # Market conditions
            "spread_bps_entry": float(self.spread_bps_entry) if self.spread_bps_entry else None,
            "imbalance_entry": float(self.imbalance_entry) if self.imbalance_entry else None,
            "depth_5bps_entry": float(self.depth_5bps_entry) if self.depth_5bps_entry else None,
            
            # Metadata
            "strategy_tag": self.strategy_tag,
            "strategy_params": self.strategy_params,
            "status": self.status,
            
            # Timestamps
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def create_entry(
        cls,
        trade_id: str,
        symbol: str,
        entry_time: datetime,
        entry_price: float,
        entry_qty: float,
        entry_side: str = "BUY",
        entry_fee: float = 0.0,
        spread_bps: Optional[float] = None,
        imbalance: Optional[float] = None,
        depth_5bps: Optional[float] = None,
        strategy_tag: Optional[str] = None,
        strategy_params: Optional[str] = None,
        exchange: str = "MEXC"
    ) -> "Trade":
        """
        Factory method to create a new trade entry (position opened).
        
        Args:
            trade_id: Unique trade identifier
            symbol: Trading symbol
            entry_time: When position opened
            entry_price: Entry price
            entry_qty: Quantity
            entry_side: BUY or SELL
            entry_fee: Entry fee paid
            spread_bps: Spread at entry
            imbalance: Order book imbalance
            depth_5bps: Depth within 5bps
            strategy_tag: Strategy identifier
            strategy_params: JSON params
            exchange: Exchange name
            
        Returns:
            New Trade instance with status OPEN
        """
        return cls(
            trade_id=trade_id,
            symbol=symbol,
            exchange=exchange,
            entry_time=entry_time,
            entry_price=entry_price,
            entry_qty=entry_qty,
            entry_side=entry_side,
            entry_fee=entry_fee,
            spread_bps_entry=spread_bps,
            imbalance_entry=imbalance,
            depth_5bps_entry=depth_5bps,
            strategy_tag=strategy_tag,
            strategy_params=strategy_params,
            status="OPEN"
        )
    
    def close_trade(
        self,
        exit_time: datetime,
        exit_price: float,
        exit_qty: float,
        exit_side: str,
        exit_reason: str,
        exit_fee: float = 0.0
    ) -> None:
        """
        Close the trade and calculate P&L.
        
        Args:
            exit_time: When position closed
            exit_price: Exit price
            exit_qty: Quantity sold
            exit_side: SELL or BUY
            exit_reason: TP, SL, TIMEOUT, MANUAL
            exit_fee: Exit fee paid
        """
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_qty = exit_qty
        self.exit_side = exit_side
        self.exit_reason = exit_reason
        self.exit_fee = exit_fee
        self.status = "CLOSED"
        
        # Calculate hold duration
        if self.entry_time and self.exit_time:
            delta = self.exit_time - self.entry_time
            self.hold_duration_sec = delta.total_seconds()
        
        # Calculate P&L
        if self.entry_price and self.exit_price and self.entry_qty:
            # For long positions (BUY → SELL)
            if self.entry_side == "BUY":
                pnl_per_unit = self.exit_price - self.entry_price
            else:
                # For short positions (SELL → BUY)
                pnl_per_unit = self.entry_price - self.exit_price
            
            gross_pnl = pnl_per_unit * self.entry_qty
            
            # Subtract fees
            self.total_fee = self.entry_fee + self.exit_fee
            self.pnl_usd = gross_pnl - self.total_fee
            
            # Calculate percentage and bps
            if self.entry_price > 0:
                self.pnl_percent = (pnl_per_unit / self.entry_price) * 100
                self.pnl_bps = self.pnl_percent * 100  # 1% = 100 bps