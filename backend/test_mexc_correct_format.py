import asyncio
import websockets
import json

async def test_correct_format():
    uri = "wss://wbs-api.mexc.com/ws"
    
    print(f"Testing correct format from documentation...")
    print(f"URL: {uri}")
    print("="*60)
    
    async with websockets.connect(uri) as ws:
        print("✅ Connected!")
        
        # CORRECT format according to documentation
        correct_topics = [
            "spot@public.aggre.bookTicker.v3.api.pb@100ms@BTCUSDT",  # With @100ms
            "spot@public.aggre.deals.v3.api.pb@100ms@BTCUSDT",       # Deals with @100ms
        ]
        
        for i, topic in enumerate(correct_topics, 1):
            sub = {
                "method": "SUBSCRIPTION",
                "params": [topic],
                "id": i
            }
            
            await ws.send(json.dumps(sub))
            print(f"\n📤 Test {i}: {topic}")
            
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                print(f"📥 Response: {msg}")
                
                if "Blocked" in msg:
                    print("❌ Still blocked")
                elif '"code":0' in msg and "Not Subscribed successfully" not in msg:
                    print("✅ SUCCESS - This format works!")
                    
            except asyncio.TimeoutError:
                print("⚠️ Timeout")
            
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(test_correct_format())
    except KeyboardInterrupt:
        print("\n⏹️ Interrupted")