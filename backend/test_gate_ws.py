"""
Test Gate.io WebSocket connection
"""
import asyncio
import logging
from app.market_data.gate_ws import GateWebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_gate_ws():
    """Test Gate.io WebSocket - 1 symbol, 30 seconds"""
    
    symbols = ["BTC_USDT"]  # Gate.io format with underscore
    
    logger.info("="*60)
    logger.info("🧪 GATE.IO WEBSOCKET TEST")
    logger.info("="*60)
    logger.info(f"Symbols: {symbols}")
    logger.info(f"Duration: 30 seconds")
    logger.info("="*60)
    
    try:
        client = GateWebSocketClient(symbols=symbols)
        
        # Start WebSocket
        ws_task = asyncio.create_task(client.run())
        
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
        
        # Get stats
        stats = client.get_stats()
        
        # Print results
        logger.info("\n" + "="*60)
        logger.info("📊 TEST RESULTS:")
        logger.info("="*60)
        logger.info(f"Connected:          {stats.get('connected', False)}")
        logger.info(f"Messages received:  {stats.get('total_messages', 0)}")
        logger.info(f"Book tickers:       {stats.get('total_book_tickers', 0)}")
        logger.info(f"Trades:             {stats.get('total_trades', 0)}")
        logger.info("="*60)
        
        # Check for issues
        if stats.get('total_book_tickers', 0) == 0:
            logger.error("❌ FAIL: No data received")
            return False
        
        logger.info("✅ PASS: Gate.io WebSocket working!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    try:
        success = asyncio.run(test_gate_ws())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("⏹️ Interrupted by user")
        exit(1)