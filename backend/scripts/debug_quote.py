import asyncio
import sys
sys.path.insert(0, '.')

from app.services import book_tracker as bt

async def debug():
    symbols = ["LINKUSDT"]
    
    print("\n" + "="*80)
    print("SUBSCRIBING SYMBOLS...")
    print("="*80)
    
    # ✅ Subscribe symbols first
    await bt.ensure_symbols_subscribed(symbols)
    
    print("✅ Subscribed, waiting 5 seconds for data...")
    await asyncio.sleep(5)
    
    print("\n" + "="*80)
    print("BOOK_TRACKER.GET_QUOTE() OUTPUT:")
    print("="*80)
    
    q = await bt.get_quote("LINKUSDT")
    
    for key, value in sorted(q.items()):
        print(f"{key:30s} = {value}")
    
    print("\n" + "="*80)
    print("IMBALANCE CALCULATION CHECK:")
    print("="*80)
    
    abs_bid = q.get('absorption_bid_usd', 0)
    abs_ask = q.get('absorption_ask_usd', 0)
    imb_reported = q.get('imbalance', 0)
    bid = q.get('bid', 0)
    ask = q.get('ask', 0)
    
    if abs_bid + abs_ask > 0:
        imb_expected = abs_bid / (abs_bid + abs_ask)
    else:
        imb_expected = 0.5
    
    print(f"Bid:                  ${bid:.6f}")
    print(f"Ask:                  ${ask:.6f}")
    print(f"absorption_bid_usd:   ${abs_bid:,.2f}")
    print(f"absorption_ask_usd:   ${abs_ask:,.2f}")
    print(f"imbalance (reported):  {imb_reported:.4f}")
    print(f"imbalance (expected):  {imb_expected:.4f}")
    
    if abs(imb_reported - imb_expected) < 0.01:
        print(f"Match: ✅ YES")
    else:
        print(f"Match: ❌ NO - CALCULATION BUG!")
    
    # Check if in tradeable range
    if abs_bid > 0 and abs_ask > 0:
        if imb_reported < 0.05:
            print(f"\n⚠️  TOO BEARISH (< 0.05) - ASK SIDE DOMINATES")
        elif imb_reported > 0.95:
            print(f"\n⚠️  TOO BULLISH (> 0.95) - BID SIDE DOMINATES")
        else:
            print(f"\n✅ IN RANGE (0.05-0.95) - TRADEABLE")
    else:
        print(f"\n❌ NO LIQUIDITY DATA - WebSocket or REST polling not working")

asyncio.run(debug())