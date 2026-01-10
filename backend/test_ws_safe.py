"""
Safe WebSocket test with resource monitoring
"""
import asyncio
import logging
import time
from app.market_data.ws_client import MEXCWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def monitor_resources(duration_sec: int = 30):
    """Monitor CPU and memory during test"""
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not installed, skipping resource monitoring")
        await asyncio.sleep(duration_sec)
        return 0.0, 0.0
    
    start_time = time.time()
    max_cpu = 0.0
    max_memory_mb = 0.0
    
    while time.time() - start_time < duration_sec:
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.Process().memory_info().rss / 1024 / 1024
        
        max_cpu = max(max_cpu, cpu)
        max_memory_mb = max(max_memory_mb, memory)
        
        if cpu > 80:
            logger.error(f"🚨 CPU spike: {cpu:.1f}%")
        
        await asyncio.sleep(1)
    
    return max_cpu, max_memory_mb

async def test_ws_safe():
    """Safe WebSocket test - 1 symbol, 30 seconds"""
    
    symbols = ["BTCUSDT"]  # Only 1 symbol for safety
    
    logger.info("="*60)
    logger.info("🧪 SAFE WEBSOCKET TEST")
    logger.info("="*60)
    logger.info(f"Symbols: {symbols}")
    logger.info(f"Duration: 30 seconds")
    logger.info(f"Channels: BOOK_TICKER only")
    logger.info("="*60)
    
    client = MEXCWebSocketClient(
        symbols=symbols,
        channels=["BOOK_TICKER"]
    )
    
    # Start WebSocket and monitoring in parallel
    ws_task = asyncio.create_task(client.run())
    monitor_task = asyncio.create_task(monitor_resources(30))
    
    # Wait 30 seconds
    await asyncio.sleep(30)
    
    # Stop WebSocket
    logger.info("⏹️ Stopping WebSocket...")
    await client.stop()
    
    # Wait for clean shutdown
    try:
        await asyncio.wait_for(ws_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.error("⚠️ WebSocket didn't stop gracefully")
        ws_task.cancel()
    
    # Get monitoring results
    max_cpu, max_memory_mb = await monitor_task
    
    # Get WebSocket stats
    stats = client.get_stats()
    
    # Print results
    logger.info("\n" + "="*60)
    logger.info("📊 TEST RESULTS:")
    logger.info("="*60)
    logger.info(f"Messages received:  {stats['total_messages']}")
    logger.info(f"Book tickers:       {stats['total_book_tickers']}")
    logger.info(f"Deals:              {stats['total_deals']}")
    logger.info(f"Depth updates:      {stats['total_depth_updates']}")
    logger.info(f"Reconnects:         {stats['total_reconnects']}")
    logger.info(f"Max CPU:            {max_cpu:.1f}%")
    logger.info(f"Max Memory:         {max_memory_mb:.1f} MB")
    logger.info("="*60)
    
    # Check for issues
    if max_cpu > 50:
        logger.error("❌ FAIL: High CPU usage (>50%)")
        return False
    
    if stats['total_book_tickers'] == 0:
        logger.error("❌ FAIL: No data received")
        return False
    
    logger.info("✅ PASS: WebSocket working safely")
    return True

if __name__ == "__main__":
    try:
        success = asyncio.run(test_ws_safe())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("⏹️ Interrupted by user")
        exit(1)
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        exit(1)