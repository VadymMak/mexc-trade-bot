import asyncio
import websockets
import json

async def test_endpoint(url, name):
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print('='*60)
    
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("✅ Connected!")
            
            # Try simple subscription
            sub = {
                "method": "SUBSCRIPTION",
                "params": ["spot@public.bookTicker.v3.api@BTCUSDT"],
                "id": 1
            }
            
            await ws.send(json.dumps(sub))
            print(f"Sent: {sub}")
            
            # Wait for response
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                print(f"Response: {msg[:200]}")
                
                # Check if blocked
                if "Blocked" in msg:
                    print("❌ BLOCKED!")
                    return False
                else:
                    print("✅ NOT BLOCKED - This endpoint works!")
                    return True
                    
            except asyncio.TimeoutError:
                print("⚠️ Timeout (no response)")
                return False
                
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

async def test_all():
    endpoints = [
        ("wss://wbs-api.mexc.com/ws", "Main (current)"),
        ("wss://ws-api.mexc.com/ws", "Alternative 1"),
        ("wss://contract.mexc.com/ws", "Contract WS"),
        ("wss://wbs.mexc.com/ws", "Alternative 2"),
        ("wss://api.mexc.com/ws", "Alternative 3"),
    ]
    
    results = []
    for url, name in endpoints:
        working = await test_endpoint(url, name)
        results.append((name, url, working))
        await asyncio.sleep(1)  # Pause between tests
    
    # Summary
    print("\n" + "="*60)
    print("📊 SUMMARY:")
    print("="*60)
    for name, url, working in results:
        status = "✅ WORKS" if working else "❌ BLOCKED"
        print(f"{status} - {name}")
        print(f"  {url}")
    print("="*60)

if __name__ == "__main__":
    try:
        asyncio.run(test_all())
    except KeyboardInterrupt:
        print("\n⏹️ Interrupted")