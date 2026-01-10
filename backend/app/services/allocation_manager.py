# app/services/allocation_manager.py
from __future__ import annotations

from typing import Dict, List, Any
from sqlalchemy.orm import Session

# Глобальная переменная для хранения режима (in-memory)
_ALLOCATION_MODE: str = "equal"  # "equal", "dynamic", or "smart"


def get_allocation_mode() -> str:
    """Get current allocation mode."""
    return _ALLOCATION_MODE


def set_allocation_mode(mode: str) -> str:
    """Set allocation mode. Returns the new mode."""
    global _ALLOCATION_MODE
    if mode not in {"dynamic", "equal", "smart"}:
        raise ValueError(f"Invalid mode: {mode}. Must be 'dynamic', 'equal', or 'smart'")
    _ALLOCATION_MODE = mode
    return _ALLOCATION_MODE


def calculate_dynamic_allocation(
    symbols: List[str],
    total_capital: float,
    position_size_usd: float,
    db: Session = None
) -> Dict[str, Any]:
    """
    Calculate dynamic allocation based on liquidity.
    
    TEMPORARY: Uses mock depth values for testing.
    TODO: Replace with real-time scanner API calls.
    """
    if not symbols:
        return {}
    
    # Mock liquidity depths (USD at 5bps)
    # These values simulate real market liquidity
    MOCK_DEPTHS = {
        "XRPUSDT": 180000,   # Very high liquidity
        "BTCUSDT": 250000,   # Highest liquidity
        "ETHUSDT": 200000,
        "AVAXUSDT": 120000,  # High liquidity
        "NEARUSDT": 80000,   # Medium liquidity
        "LINKUSDT": 70000,
        "ADAUSDT": 50000,    # Lower liquidity
        "ALGOUSDT": 40000,
        "VETUSDT": 10000,    # Low liquidity
        "DOTUSDT": 90000,
        "MATICUSDT": 60000,
    }
    
    # Get depth for each symbol (use mock or default)
    depth_map = {}
    for symbol in symbols:
        depth_map[symbol] = MOCK_DEPTHS.get(symbol, 50000.0)  # Default 50k
    
    total_depth = sum(depth_map.values())
    
    if total_depth == 0:
        return calculate_equal_allocation(symbols, total_capital, position_size_usd)
    
    # Allocate proportionally to liquidity
    allocations = {}
    for symbol in symbols:
        depth = depth_map[symbol]
        allocation_pct = (depth / total_depth) * 100
        allocated_usd = (allocation_pct / 100) * total_capital
        max_positions = int(allocated_usd / position_size_usd) if position_size_usd > 0 else 0
        
        allocations[symbol] = {
            "allocated_usd": round(allocated_usd, 2),
            "allocation_pct": round(allocation_pct, 1),
            "max_positions": max_positions,
            "depth_5bps": round(depth, 2),
        }
    
    print(f"[ALLOCATION] Dynamic (MOCK) allocation: {len(symbols)} symbols")
    for sym, alloc in allocations.items():
        print(f"  {sym}: ${alloc['allocated_usd']:.0f} ({alloc['allocation_pct']:.1f}%) - depth ${alloc['depth_5bps']:.0f}")
    
    return allocations


def calculate_equal_allocation(
    symbols: List[str],
    total_capital: float,
    position_size_usd: float
) -> Dict[str, Any]:
    """
    Calculate equal allocation across all symbols.
    
    Splits capital evenly between symbols.
    """
    if not symbols:
        return {}
    
    num_symbols = len(symbols)
    allocation_pct = 100.0 / num_symbols
    allocated_usd = total_capital / num_symbols
    max_positions = int(allocated_usd / position_size_usd) if position_size_usd > 0 else 0
    
    allocations = {}
    for symbol in symbols:
        allocations[symbol] = {
            "allocated_usd": round(allocated_usd, 2),
            "allocation_pct": round(allocation_pct, 1),
            "max_positions": max_positions,
            "depth_5bps": None,  # No depth data in equal mode
        }
    
    return allocations

def calculate_smart_allocation(
    symbols: List[str],
    total_capital: float,
    position_size_usd: float,
    db: Session = None
) -> Dict[str, Any]:
    """
    Calculate SMART allocation based on historical performance.
    
    Score formula:
    - Win Rate: 40%
    - Avg PnL (bps): 30%
    - Liquidity (depth): 20%
    - Spread quality: 10%
    
    Symbols with better historical performance get more capital.
    """
    if not symbols:
        return {}
    
    if db is None:
        # Fallback to equal if no DB
        print("[ALLOCATION] No DB connection, falling back to equal")
        return calculate_equal_allocation(symbols, total_capital, position_size_usd)
    
    try:
        from app.models.trades import Trade
        from sqlalchemy import func
        
        # Calculate scores for each symbol
        scores = {}
        
        # Mock liquidity (same as dynamic)
        MOCK_DEPTHS = {
            "XRPUSDT": 180000, "BTCUSDT": 250000, "ETHUSDT": 200000,
            "AVAXUSDT": 120000, "NEARUSDT": 80000, "LINKUSDT": 70000,
            "ADAUSDT": 50000, "ALGOUSDT": 40000, "VETUSDT": 10000,
            "DOTUSDT": 90000, "MATICUSDT": 60000,
        }
        
        for symbol in symbols:
            # Get historical stats from last 100 trades
            trades = (
                db.query(Trade)
                .filter(Trade.symbol == symbol)
                .filter(Trade.status == "CLOSED")
                .order_by(Trade.created_at.desc())
                .limit(100)
                .all()
            )
            
            if not trades or len(trades) < 5:
                # Not enough data, use neutral score
                scores[symbol] = 50.0  # Neutral
                print(f"[ALLOCATION] {symbol}: Not enough data ({len(trades) if trades else 0} trades), neutral score")
                continue
            
            # Calculate metrics
            total_trades = len(trades)
            winning_trades = sum(1 for t in trades if t.pnl_usd and t.pnl_usd > 0)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 50.0
            
            avg_pnl_bps = sum(t.pnl_bps for t in trades if t.pnl_bps) / total_trades if total_trades > 0 else 0.0
            
            # Get liquidity
            depth = MOCK_DEPTHS.get(symbol, 50000.0)
            liquidity_score = min(depth / 100000 * 100, 100)  # Normalize to 0-100
            
            # Get avg spread (higher is better for our strategy)
            avg_spread = sum(t.spread_bps_entry for t in trades if t.spread_bps_entry) / total_trades if total_trades > 0 else 5.0
            spread_score = min(avg_spread / 10 * 100, 100)  # Normalize to 0-100
            
            # Calculate composite score
            score = (
                win_rate * 0.4 +           # 40% weight on win rate
                (avg_pnl_bps + 10) * 0.3 + # 30% weight on avg PnL (shifted by +10 to avoid negatives)
                liquidity_score * 0.2 +     # 20% weight on liquidity
                spread_score * 0.1          # 10% weight on spread
            )
            
            scores[symbol] = max(score, 1.0)  # Minimum score of 1
            
            print(f"[ALLOCATION] {symbol}: WR={win_rate:.1f}% PnL={avg_pnl_bps:.2f}bps Spread={avg_spread:.1f}bps → Score={score:.1f}")
        
        # Allocate based on scores
        total_score = sum(scores.values())
        
        if total_score == 0:
            return calculate_equal_allocation(symbols, total_capital, position_size_usd)
        
        allocations = {}
        for symbol in symbols:
            score = scores[symbol]
            allocation_pct = (score / total_score) * 100
            allocated_usd = (allocation_pct / 100) * total_capital
            max_positions = int(allocated_usd / position_size_usd) if position_size_usd > 0 else 0
            
            # Get depth for display
            depth = MOCK_DEPTHS.get(symbol, 50000.0)
            
            allocations[symbol] = {
                "allocated_usd": round(allocated_usd, 2),
                "allocation_pct": round(allocation_pct, 1),
                "max_positions": max_positions,
                "depth_5bps": round(depth, 2),
                "smart_score": round(score, 1),  # NEW: show the score
            }
        
        print(f"[ALLOCATION] Smart allocation: {len(symbols)} symbols based on historical performance")
        return allocations
        
    except Exception as e:
        print(f"[WARN] Smart allocation failed: {e}, falling back to equal")
        import traceback
        traceback.print_exc()
        return calculate_equal_allocation(symbols, total_capital, position_size_usd)


def calculate_allocation(
    symbols: List[str],
    total_capital: float,
    position_size_usd: float,
    mode: str = None,
    db: Session = None
) -> Dict[str, Any]:
    """
    Calculate allocation based on current mode.
    
    Modes:
    - equal: Split capital evenly
    - dynamic: Based on liquidity (depth@5bps)
    - smart: Based on historical performance (win rate + avg PnL + liquidity + spread)
    
    If mode is not provided, uses global _ALLOCATION_MODE.
    """
    if mode is None:
        mode = get_allocation_mode()
    
    if mode == "dynamic":
        return calculate_dynamic_allocation(symbols, total_capital, position_size_usd, db)
    elif mode == "smart":
        return calculate_smart_allocation(symbols, total_capital, position_size_usd, db)
    else:
        return calculate_equal_allocation(symbols, total_capital, position_size_usd)