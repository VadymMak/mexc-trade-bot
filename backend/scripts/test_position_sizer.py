"""Test position sizer"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from app.services.position_sizer import get_position_sizer, SizingMode
from app.services.mm_detector import get_mm_detector

async def test_position_sizer():
    print("Testing Position Sizer...")
    print("=" * 60)
    
    sizer = get_position_sizer()
    detector = get_mm_detector()
    
    symbol = "LINKUSDT"
    
    # First, create MM pattern (NEED MORE SAMPLES!)
    print("\n1. Creating MM pattern (need 50+ samples)...")
    
    # 50 snapshots with stable MM
    for i in range(50):
        await detector.on_book_update(
            symbol=symbol,
            best_bid=70.58,
            best_ask=70.60,
            bid_size=3.0,
            ask_size=3.0
        )
        await asyncio.sleep(0.01)  # Small delay
    
    # Check detection
    pattern = detector.detect_pattern(symbol)
    if pattern:
        print(f"   ✅ MM detected!")
        print(f"   MM capacity: ${pattern.mm_avg_order_size:.2f}")
        print(f"   Confidence: {pattern.mm_confidence:.1%}")
    else:
        print(f"   ⚠️ MM not detected (not enough confidence)")
        print(f"   Will use default sizing")
    
    # Test 1: Small order (no split needed)
    print(f"\n2. Test: Small order ($5)")
    print("   " + "-" * 56)
    
    size1 = sizer.calculate_size(symbol, 5.0, SizingMode.CONSERVATIVE)
    summary1 = sizer.get_summary(size1)
    
    print(f"   Target: ${summary1['target_size_usd']}")
    print(f"   Safe size: ${summary1['safe_size_usd']}")
    print(f"   Split needed: {summary1['split_needed']}")
    print(f"   MM detected: {summary1['mm_detected']}")
    if summary1['split_needed']:
        print(f"   Split count: {summary1['split_count']}")
    print(f"   Reasoning: {summary1['reasoning']}")
    
    # Test 2: Large order (split needed)
    print(f"\n3. Test: Large order ($500)")
    print("   " + "-" * 56)
    
    size2 = sizer.calculate_size(symbol, 500.0, SizingMode.CONSERVATIVE)
    summary2 = sizer.get_summary(size2)
    
    print(f"   Target: ${summary2['target_size_usd']}")
    print(f"   Safe size: ${summary2['safe_size_usd']}")
    print(f"   Split needed: {summary2['split_needed']}")
    print(f"   MM detected: {summary2['mm_detected']}")
    if summary2['split_needed']:
        print(f"   Split count: {summary2['split_count']}")
        print(f"   Delay: {summary2['split_delay_sec']}s")
    print(f"   Reasoning: {summary2['reasoning']}")
    
    # Test 3: Different modes (only if MM detected)
    if pattern:
        print(f"\n4. Test: Same order, different modes ($100)")
        print("   " + "-" * 56)
        
        for mode in [SizingMode.CONSERVATIVE, SizingMode.BALANCED, SizingMode.AGGRESSIVE]:
            size = sizer.calculate_size(symbol, 100.0, mode)
            summary = sizer.get_summary(size)
            
            print(f"\n   Mode: {mode.value.upper()}")
            print(f"     Safe size: ${summary['safe_size_usd']}")
            print(f"     Split count: {summary['split_count']}")
            print(f"     Risk: {summary['risk_level']}")
            print(f"     MM capacity used: {(summary['safe_size_usd']/pattern.mm_avg_order_size)*100:.0f}%")
    
    # Test 4: No MM pattern
    print(f"\n5. Test: Symbol without MM pattern")
    print("   " + "-" * 56)
    
    size4 = sizer.calculate_size("UNKNOWN", 10.0, SizingMode.CONSERVATIVE)
    summary4 = sizer.get_summary(size4)
    
    print(f"   MM detected: {summary4['mm_detected']}")
    print(f"   Safe size: ${summary4['safe_size_usd']}")
    print(f"   Reasoning: {summary4['reasoning']}")
    
    print("\n✅ Test complete!")

if __name__ == '__main__':
    asyncio.run(test_position_sizer())