"""
Trade history and logs API.
Provides endpoints for retrieving trade logs with cost analysis.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import datetime, timedelta

from app.models.trades import Trade
from app.db.session import SessionLocal
from app.services.cost_tracker import get_cost_tracker
from sqlalchemy import desc, func

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/recent")
async def get_recent_trades(
    limit: int = Query(50, ge=1, le=500, description="Number of trades to return"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: Optional[str] = Query(None, description="Filter by status: OPEN, CLOSED"),
    period: str = Query("all", description="today | wtd | mtd | all")
) -> List[dict]:
    """
    Get recent trade history.
    
    Returns list of trades with entry/exit details, P&L, and metadata.
    """
    db = SessionLocal()
    try:
        query = db.query(Trade).order_by(desc(Trade.entry_time))
        
        # Filter by symbol
        if symbol:
            query = query.filter(Trade.symbol == symbol.upper())
        
        # Filter by status
        if status:
            query = query.filter(Trade.status == status.upper())
        
        # Filter by period
        if period == "today":
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Trade.entry_time >= today_start)
        elif period == "wtd":
            week_start = datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Trade.entry_time >= week_start)
        elif period == "mtd":
            month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Trade.entry_time >= month_start)
        
        trades = query.limit(limit).all()
        
        return [t.to_dict() for t in trades]
        
    finally:
        db.close()


@router.get("/stats")
async def get_trade_stats(
    period: str = Query("today", description="today | wtd | mtd | all"),
    include_costs: bool = Query(True, description="Include infrastructure cost analysis")
) -> dict:
    """
    Get trading statistics with optional cost analysis.
    
    Returns:
        Aggregated stats: total trades, win rate, P&L, net profit after costs
    """
    db = SessionLocal()
    try:
        # Determine date range
        if period == "today":
            start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            days = 1
        elif period == "wtd":
            start = datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            days = datetime.utcnow().weekday() + 1
        elif period == "mtd":
            start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            days = datetime.utcnow().day
        else:  # all
            start = datetime(2020, 1, 1)
            days = (datetime.utcnow() - start).days
        
        # Get trades
        trades = db.query(Trade).filter(Trade.entry_time >= start).all()
        
        closed_trades = [t for t in trades if t.status == "CLOSED"]
        
        total_trades = len(closed_trades)
        winning_trades = len([t for t in closed_trades if t.pnl_usd and t.pnl_usd > 0])
        losing_trades = len([t for t in closed_trades if t.pnl_usd and t.pnl_usd <= 0])
        
        total_pnl = sum(t.pnl_usd for t in closed_trades if t.pnl_usd)
        total_fees = sum(t.total_fee for t in closed_trades if t.total_fee)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        avg_profit = total_pnl / total_trades if total_trades > 0 else 0.0
        
        # Best/worst trades
        profits = [t.pnl_usd for t in closed_trades if t.pnl_usd]
        best_trade = max(profits) if profits else 0.0
        worst_trade = min(profits) if profits else 0.0
        
        # Average duration
        durations = [t.hold_duration_sec for t in closed_trades if t.hold_duration_sec]
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        
        result = {
            "period": period,
            "days": days,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "trading_pnl": round(total_pnl, 2),
            "trading_fees": round(-total_fees, 2),
            "gross_profit": round(total_pnl - total_fees, 2),
            "avg_profit_per_trade": round(avg_profit, 4),
            "avg_duration_sec": round(avg_duration, 2),
            "best_trade": round(best_trade, 4),
            "worst_trade": round(worst_trade, 4)
        }
        
        # Add cost analysis
        if include_costs:
            tracker = get_cost_tracker()
            analysis = tracker.calculate_net_profit(
                trading_pnl=total_pnl,
                trading_fees=-total_fees,
                period_days=days
            )
            
            result.update({
                "infrastructure_costs": analysis["infrastructure_costs"],
                "net_profit": analysis["net_profit"],
                "costs_covered": analysis["costs_covered"],
                "breakeven_days": analysis["breakeven_days"]
            })
        
        return result
        
    finally:
        db.close()


@router.get("/live")
async def get_live_status() -> dict:
    """
    Get current live trading status (last 5 minutes).
    Quick endpoint for dashboard updates.
    """
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(minutes=5)
        recent = db.query(Trade).filter(Trade.entry_time >= since).order_by(desc(Trade.entry_time)).all()
        
        if not recent:
            return {
                "active": False,
                "last_trade": None,
                "recent_count": 0,
                "recent_pnl": 0.0
            }
        
        last_trade = recent[0]
        recent_pnl = sum(t.pnl_usd for t in recent if t.pnl_usd and t.status == "CLOSED")
        
        return {
            "active": True,
            "last_trade": last_trade.to_dict(),
            "recent_count": len(recent),
            "recent_pnl": round(recent_pnl, 2)
        }
        
    finally:
        db.close()


@router.get("/by-symbol/{symbol}")
async def get_symbol_trades(
    symbol: str,
    limit: int = Query(20, ge=1, le=100)
) -> dict:
    """
    Get trade history for specific symbol.
    """
    db = SessionLocal()
    try:
        sym = symbol.upper()
        trades = db.query(Trade).filter(Trade.symbol == sym).order_by(desc(Trade.entry_time)).limit(limit).all()
        
        closed = [t for t in trades if t.status == "CLOSED"]
        total_pnl = sum(t.pnl_usd for t in closed if t.pnl_usd)
        win_rate = len([t for t in closed if t.pnl_usd and t.pnl_usd > 0]) / len(closed) * 100 if closed else 0
        
        return {
            "symbol": sym,
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "trades": [t.to_dict() for t in trades]
        }
        
    finally:
        db.close()

@router.get("/export")
async def export_trades_csv(
    period: str = Query("today", description="today | wtd | mtd | all"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: Optional[str] = Query(None, description="Filter by status: OPEN, CLOSED"),
) -> str:
    """
    Export trades to CSV format.
    
    Returns CSV file with trade history.
    """
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    
    db = SessionLocal()
    try:
        # Determine date range (same logic as stats)
        if period == "today":
            start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "wtd":
            start = datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "mtd":
            start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:  # all
            start = datetime(2020, 1, 1)
        
        # Build query
        query = db.query(Trade).filter(Trade.entry_time >= start).order_by(desc(Trade.entry_time))
        
        if symbol:
            query = query.filter(Trade.symbol == symbol.upper())
        
        if status:
            query = query.filter(Trade.status == status.upper())
        
        trades = query.all()
        
        # Create CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Header
        # Header
        writer.writerow([
            "Entry Time",
            "Exit Time",
            "Symbol",
            "Entry Side",  # ✅ ИСПРАВЛЕНО
            "Status",
            "Entry Price",
            "Exit Price",
            "Entry Qty",   # ✅ ИСПРАВЛЕНО
            "P&L USD",
            "P&L %",
            "Total Fee",
            "Hold Duration (sec)",
            "Exit Reason",
            "Strategy Tag"
        ])
        
        # Data rows
        # Data rows
        for t in trades:
            writer.writerow([
                t.entry_time.isoformat() if t.entry_time else "",
                t.exit_time.isoformat() if t.exit_time else "",
                t.symbol or "",
                t.entry_side or "",  # ✅ ИСПРАВЛЕНО: entry_side вместо side
                t.status or "",
                f"{t.entry_price:.8f}" if t.entry_price else "",
                f"{t.exit_price:.8f}" if t.exit_price else "",
                f"{t.entry_qty:.8f}" if t.entry_qty else "",  # ✅ ИСПРАВЛЕНО: entry_qty вместо quantity
                f"{t.pnl_usd:.4f}" if t.pnl_usd else "",
                f"{t.pnl_percent:.2f}" if t.pnl_percent else "",
                f"{t.total_fee:.4f}" if t.total_fee else "",
                f"{t.hold_duration_sec:.2f}" if t.hold_duration_sec else "",
                t.exit_reason or "",
                t.strategy_tag or ""
            ])
        
        # Prepare response
        output.seek(0)
        filename = f"trades_{period}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
        
    finally:
        db.close()