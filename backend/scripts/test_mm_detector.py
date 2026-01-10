"""Test MM detector"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from app.services.mm_detector import get_mm_detector

async def test_mm():
    detector = get_mm_detector()
    
    print("Testing MM Detector...")
    print("=" * 60)
    
    symbol = "LINKUSDT"
    
    # Simulate MM pattern: stable bid/ask around 70.58/70.60
    print("\nSimulating MM pattern (stable boundaries)...")
    
    # MM keeps bid at 70.58, ask at 70.60
    for i in range(30):
        await detector.on_book_update(
            symbol=symbol,
            best_bid=70.58,
            best_ask=70.60,
            bid_size=3.0,
            ask_size=3.0
        )
        await asyncio.sleep(0.1)  # 100ms between updates
    
    # MM occasionally moves slightly
    for i in range(10):
        await detector.on_book_update(
            symbol=symbol,
            best_bid=70.57,
            best_ask=70.59,
            bid_size=2.8,
            ask_size=3.2
        )
        await asyncio.sleep(0.1)
    
    # Back to main levels
    for i in range(10):
        await detector.on_book_update(
            symbol=symbol,
            best_bid=70.58,
            best_ask=70.60,
            bid_size=3.0,
            ask_size=3.0
        )
        await asyncio.sleep(0.1)
    
    print(f"Processed 50 book updates")
    
    # Detect pattern
    print(f"\nDetecting MM pattern...")
    pattern = detector.detect_pattern(symbol)
    
    if pattern:
        print(f"\n✅ MM Pattern Detected!")
        print(f"  Confidence: {pattern.mm_confidence:.1%}")
        print(f"  Lower bound: ${pattern.mm_lower_bound:.4f}")
        print(f"  Upper bound: ${pattern.mm_upper_bound:.4f}")
        print(f"  Avg order size: ${pattern.mm_avg_order_size:.2f}")
        print(f"  Refresh rate: {pattern.mm_refresh_rate:.2f} Hz")
        print(f"  Spread: {pattern.mm_spread_bps:.1f} bps")
        print(f"\n  Recommendations:")
        print(f"    Best entry (BUY):  ${pattern.best_entry_price:.4f}")
        print(f"    Best exit (SELL):  ${pattern.best_exit_price:.4f}")
        print(f"    Safe order size:   ${pattern.safe_order_size_usd:.2f}")
    else:
        print(f"\n❌ No MM pattern detected")
    
    # Summary
    print(f"\nSummary:")
    summary = detector.get_summary(symbol)
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    print("\n✅ Test complete!")

if __name__ == '__main__':
    asyncio.run(test_mm())