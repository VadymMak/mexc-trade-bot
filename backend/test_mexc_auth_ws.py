import asyncio
import websockets
import json
import hmac
import hashlib
import time

# Твои ключи из .env
API_KEY = "mx0vglMqAeHTfRZPYR"
API_SECRET = "0dcae79f3a0f4cc29378b74a98b88d45"
UI_ID = "WEB1492f7dc210631f57ac5dca5e17c3f3516142645aeebdeba4c17976a150421ec"

def create_signature(params_str: str) -> str:
    """Create HMAC SHA256 signature"""
    return hmac.new(
        API_SECRET.encode('utf-8'),
        params_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

async def test_auth_connection():
    uri = "wss://wbs-api.mexc.com/ws"
    
    print(f"🔐 Testing AUTHENTICATED connection to {uri}")
    print(f"API Key: {API_KEY}")
    print(f"UI ID: {UI_ID[:50]}...")
    print("="*60)
    
    # Create custom headers with authentication
    headers = {
        "X-MEXC-APIKEY": API_KEY,
    }
    
    try:
        async with websockets.connect(uri, extra_headers=headers) as ws:
            print("✅ Connected with API Key header!")
            
            # Method 1: Try subscribing with API key in message
            timestamp = str(int(time.time() * 1000))
            
            # Create authenticated subscription
            sub1 = {
                "method": "SUBSCRIPTION",
                "params": ["spot@public.bookTicker.v3.api@BTCUSDT"],
                "id": 1,
                "apiKey": API_KEY,
            }
            
            await ws.send(json.dumps(sub1))
            print(f"\n📤 Sent (with apiKey): {sub1}")
            
            msg1 = await asyncio.wait_for(ws.recv(), timeout=3.0)
            print(f"📥 Response 1: {msg1}")
            
            if "Blocked" not in msg1:
                print("\n🎉 SUCCESS! API Key in message works!")
                return True
            
            # Method 2: Try authentication message first
            auth_msg = {
                "method": "LOGIN",
                "params": {
                    "apiKey": API_KEY,
                    "reqTime": timestamp,
                    "signature": create_signature(f"apiKey={API_KEY}&reqTime={timestamp}")
                },
                "id": 2
            }
            
            await ws.send(json.dumps(auth_msg))
            print(f"\n📤 Sent LOGIN: {auth_msg}")
            
            msg2 = await asyncio.wait_for(ws.recv(), timeout=3.0)
            print(f"📥 Response 2: {msg2}")
            
            # Now try subscription after auth
            sub2 = {
                "method": "SUBSCRIPTION",
                "params": ["spot@public.bookTicker.v3.api@BTCUSDT"],
                "id": 3
            }
            
            await ws.send(json.dumps(sub2))
            print(f"\n📤 Sent (after LOGIN): {sub2}")
            
            msg3 = await asyncio.wait_for(ws.recv(), timeout=3.0)
            print(f"📥 Response 3: {msg3}")
            
            if "Blocked" not in msg3:
                print("\n🎉 SUCCESS! LOGIN method works!")
                return True
            else:
                print("\n❌ Still blocked after authentication")
                return False
                
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_private_ws():
    """Try private WebSocket endpoint (if different)"""
    uri = "wss://wbs-api.mexc.com/ws"
    
    print(f"\n{'='*60}")
    print("🔐 Testing PRIVATE WebSocket endpoint")
    print("="*60)
    
    timestamp = str(int(time.time() * 1000))
    
    # Create signature for authentication
    params = f"apiKey={API_KEY}&reqTime={timestamp}"
    signature = create_signature(params)
    
    # Try connecting with query parameters
    auth_uri = f"{uri}?apiKey={API_KEY}&reqTime={timestamp}&signature={signature}"
    
    try:
        async with websockets.connect(auth_uri) as ws:
            print("✅ Connected with auth in URL!")
            
            sub = {
                "method": "SUBSCRIPTION",
                "params": ["spot@public.bookTicker.v3.api@BTCUSDT"],
                "id": 1
            }
            
            await ws.send(json.dumps(sub))
            print(f"📤 Sent: {sub}")
            
            msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            print(f"📥 Response: {msg}")
            
            if "Blocked" not in msg:
                print("\n🎉 SUCCESS! Auth in URL works!")
                return True
            else:
                print("\n❌ Still blocked")
                return False
                
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    print("🧪 MEXC AUTHENTICATED WEBSOCKET TEST")
    print("="*60)
    
    try:
        # Test 1: Standard WS with auth
        result1 = asyncio.run(test_auth_connection())
        
        # Test 2: Private WS endpoint
        if not result1:
            result2 = asyncio.run(test_private_ws())
            
            if result2:
                print("\n✅ Found working method: Private WS with auth in URL")
            else:
                print("\n❌ All authentication methods failed")
        else:
            print("\n✅ Found working method: API Key in message")
            
    except KeyboardInterrupt:
        print("\n⏹️ Interrupted")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")