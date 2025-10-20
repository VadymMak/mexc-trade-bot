"""
Quick test of the PricePoller service.
Run: python -m scripts.test_price_poller
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.price_poller import get_poller


async def main():
    poller = get_poller()
    
    # Test with a few symbols
    test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    print(f"🧪 Testing PricePoller with: {test_symbols}")
    print("⏳ Starting poller...")
    
    await poller.start(test_symbols)
    
    # Wait a bit for first poll
    await asyncio.sleep(4)
    
    # Check prices
    print("\n📊 Cached prices:")
    for symbol in test_symbols:
        price = poller.get_price(symbol)
        if price:
            print(f"  {symbol}: bid={price['bid']:.6f}, ask={price['ask']:.6f}, mid={price['mid']:.6f}")
        else:
            print(f"  {symbol}: ❌ No price data")
    
    # Check get_mid helper
    print("\n📈 Testing get_mid():")
    for symbol in test_symbols:
        mid = poller.get_mid(symbol)
        print(f"  {symbol}: {mid:.6f}" if mid else f"  {symbol}: ❌ None")
    
    # Let it run for 10 seconds
    print("\n⏱️ Polling for 10 seconds...")
    await asyncio.sleep(10)
    
    # Final check
    print("\n📊 Final prices:")
    for symbol in test_symbols:
        price = poller.get_price(symbol)
        if price:
            age = asyncio.get_event_loop().time() - price['timestamp']
            print(f"  {symbol}: mid={price['mid']:.6f} (age: {age:.1f}s)")
    
    await poller.stop()
    print("\n✅ Test complete!")


if __name__ == "__main__":
    asyncio.run(main())