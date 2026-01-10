import asyncio
import websockets
import json

async def test_mexc():
    uri = "wss://wbs-api.mexc.com/ws"
    
    print(f"Connecting to {uri}...")
    async with websockets.connect(uri) as ws:
        print("✅ Connected!")
        
        # Try different topic formats
        topics_to_try = [
            "spot@public.bookTicker.v3.api@BTCUSDT",           # Original
            "spot@public.deals.v3.api@BTCUSDT",                # Deals
            {"method": "SUBSCRIBE", "params": ["btcusdt@bookTicker"]},  # Binance-style
            "btcusdt@bookTicker",                               # Simple format
            "BTCUSDT@bookTicker",                               # Uppercase
        ]
        
        for i, topic in enumerate(topics_to_try, 1):
            print(f"\n--- Test {i} ---")
            
            if isinstance(topic, str):
                sub = {
                    "method": "SUBSCRIPTION",
                    "params": [topic],
                    "id": i
                }
            else:
                sub = topic
                sub["id"] = i
            
            await ws.send(json.dumps(sub))
            print(f"Sent: {json.dumps(sub)}")
            
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                print(f"Response: {msg}")
            except asyncio.TimeoutError:
                print("No response (timeout)")
            
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(test_mexc())
    except Exception as e:
        print(f"❌ Error: {e}")