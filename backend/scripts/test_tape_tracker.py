"""Test tape tracker"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from app.services.tape_tracker import get_tape_tracker

async def test_tape():
    tracker = get_tape_tracker()
    
    print("Testing Tape Tracker...")
    print("=" * 60)
    
    # Simulate some trades
    symbol = "LINKUSDT"
    
    # BUY trades (price at ask)
    await tracker.on_trade(symbol, 70.60, 3.0, best_bid=70.59, best_ask=70.60)
    await tracker.on_trade(symbol, 70.61, 2.5, best_bid=70.60, best_ask=70.61)
    await tracker.on_trade(symbol, 70.62, 4.0, best_bid=70.61, best_ask=70.62)
    
    # SELL trades (price at bid)
    await tracker.on_trade(symbol, 70.59, 2.0, best_bid=70.59, best_ask=70.60)
    await tracker.on_trade(symbol, 70.58, 1.5, best_bid=70.58, best_ask=70.59)
    
    # Large trade (whale)
    await tracker.on_trade(symbol, 70.60, 20.0, best_bid=70.59, best_ask=70.60)  # $1,412
    
    # Get metrics
    metrics = tracker.get_metrics(symbol)
    
    print(f"\nMetrics for {symbol}:")
    print(f"  Total trades: {metrics.total_trades}")
    print(f"  Buy trades: {metrics.buy_trades}")
    print(f"  Sell trades: {metrics.sell_trades}")
    print(f"  Large trades: {metrics.large_trades}")
    print(f"  Aggressor ratio: {metrics.aggressor_ratio:.1%}")
    print(f"  Buy pressure: {metrics.buy_pressure:.1%}")
    print(f"  Trades/sec: {metrics.trades_per_sec:.2f}")
    
    # Summary
    print(f"\nSummary:")
    summary = tracker.get_summary(symbol)
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    print("\n✅ Test complete!")

if __name__ == '__main__':
    asyncio.run(test_tape())