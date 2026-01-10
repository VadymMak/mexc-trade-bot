"""Test enhanced book tracker"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from app.services.book_tracker_enhanced import get_enhanced_book_tracker

async def test_book_tracker():
    print("Testing Enhanced Book Tracker...")
    print("=" * 60)
    
    tracker = get_enhanced_book_tracker()
    symbol = "LINKUSDT"
    
    # Test 1: Normal stable book
    print("\n1. Simulating stable order book (30 updates)...")
    
    for i in range(30):
        bids = [
            (70.58, 3.0),
            (70.57, 2.5),
            (70.56, 2.0)
        ]
        asks = [
            (70.60, 3.0),
            (70.61, 2.5),
            (70.62, 2.0)
        ]
        tracker.on_book_update(symbol, bids, asks)
        await asyncio.sleep(0.1)
    
    metrics1 = tracker.get_metrics(symbol)
    print(f"   Avg order lifetime: {metrics1.avg_order_lifetime_sec:.2f}s")
    print(f"   Spoofing score: {metrics1.spoofing_score:.2f}")
    print(f"   Spread stability: {metrics1.spread_stability_score:.2f}")
    
    # Test 2: Spoofing (orders appear/disappear quickly)
    print("\n2. Simulating spoofing (quick orders)...")
    
    for i in range(10):
        # Large order appears
        bids = [
            (70.58, 50.0),  # LARGE!
            (70.57, 2.5),
        ]
        asks = [
            (70.60, 3.0),
            (70.61, 2.5),
        ]
        tracker.on_book_update(symbol, bids, asks)
        await asyncio.sleep(0.2)
        
        # Large order disappears (spoof!)
        bids = [
            (70.57, 2.5),  # Gone!
        ]
        asks = [
            (70.60, 3.0),
            (70.61, 2.5),
        ]
        tracker.on_book_update(symbol, bids, asks)
        await asyncio.sleep(0.2)
    
    metrics2 = tracker.get_metrics(symbol)
    spoof_signals = tracker.get_spoofing_signals(symbol)
    
    print(f"   Spoofing score: {metrics2.spoofing_score:.2f}")
    print(f"   Spoof orders detected: {len(spoof_signals)}")
    
    if spoof_signals:
        print(f"\n   Recent spoof signals:")
        for sig in spoof_signals[:3]:
            print(f"     - ${sig.price:.2f} {sig.side}: {sig.reason}")
    
    # Test 3: Volatile spread
    print("\n3. Simulating volatile spread...")
    
    for i in range(20):
        # Spread varies wildly
        spread = 0.02 + (i % 5) * 0.01  # 2-6 bps
        mid = 70.59
        bid = mid - spread/2
        ask = mid + spread/2
        
        bids = [(bid, 3.0)]
        asks = [(ask, 3.0)]
        tracker.on_book_update(symbol, bids, asks)
        await asyncio.sleep(0.1)
    
    metrics3 = tracker.get_metrics(symbol)
    print(f"   Spread stability: {metrics3.spread_stability_score:.2f}")
    print(f"   Avg spread: {metrics3.avg_spread_bps:.1f} bps")
    print(f"   Spread changes/min: {metrics3.spread_changes_per_min:.1f}")
    
    # Summary
    print(f"\n4. Final Summary:")
    summary = tracker.get_summary(symbol)
    for key, value in summary.items():
        print(f"   {key}: {value}")
    
    print("\n✅ Test complete!")

if __name__ == '__main__':
    asyncio.run(test_book_tracker())