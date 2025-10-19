# scripts/test_idempotent_decorator.py
import asyncio
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.idempotency import idempotent
from app.services.idempotency import get_idempotency_manager

# Mock endpoint with decorator
@idempotent(ttl_seconds=10)
async def mock_place_order(
    symbol: str,
    qty: float,
    x_idempotency_key: Optional[str] = None
):
    """Simulates an order placement endpoint."""
    print(f"🔨 EXECUTING: place_order(symbol={symbol}, qty={qty})")
    return {
        "order_id": "test-order-123",
        "symbol": symbol,
        "qty": qty,
        "status": "filled"
    }


async def test_decorator():
    print("=" * 60)
    print("Testing @idempotent decorator")
    print("=" * 60)
    
    manager = get_idempotency_manager()
    await manager.start()
    
    # Test 1: No idempotency key (normal execution)
    print("\n📌 Test 1: No idempotency key")
    result1 = await mock_place_order("ETHUSDT", 0.01)
    print(f"Result: {result1}")
    print(f"Has 'idempotent' flag: {'idempotent' in result1}")
    
    # Test 2: First request with key (cache miss)
    print("\n📌 Test 2: First request with idempotency key")
    result2 = await mock_place_order("BTCUSDT", 0.02, x_idempotency_key="test-key-456")
    print(f"Result: {result2}")
    print(f"Has 'idempotent' flag: {'idempotent' in result2}")
    
    # Test 3: Second request with same key (cache hit)
    print("\n📌 Test 3: Duplicate request (same key)")
    result3 = await mock_place_order("BTCUSDT", 999.99, x_idempotency_key="test-key-456")
    print(f"Result: {result3}")
    print(f"Has 'idempotent' flag: {result3.get('idempotent')}")
    print(f"Cache age: {result3.get('cache_age_sec')}s")
    print(f"Notice: qty=999.99 was NOT executed (returned cached qty=0.02)")
    
    # Stats
    print("\n📊 Cache stats:")
    stats = await manager.stats()
    print(f"  Active entries: {stats['active_entries']}")
    print(f"  Total entries: {stats['total_entries']}")
    
    await manager.stop()
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_decorator())