"""
ML Data Collector - Standalone script for collecting training data.
Subscribes to MEXC WebSocket, tracks order books, writes to ml_snapshots table.
"""
import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import websockets

import config

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('collector.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class OrderBook:
    """Simple order book tracker."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids = []  # [(price, qty), ...]
        self.asks = []
        self.last_update = 0
    
    def update(self, data: dict):
        """Update order book from snapshot."""
        self.bids = [(float(p), float(q)) for p, q in data.get('bids', [])]
        self.asks = [(float(p), float(q)) for p, q in data.get('asks', [])]
        self.last_update = time.time()
    
    def get_best_bid(self):
        """Get best bid price."""
        return self.bids[0][0] if self.bids else None
    
    def get_best_ask(self):
        """Get best ask price."""
        return self.asks[0][0] if self.asks else None
    
    def get_mid(self):
        """Calculate mid price."""
        bid = self.get_best_bid()
        ask = self.get_best_ask()
        if bid and ask:
            return (bid + ask) / 2
        return None

class MLCollector:
    """Main collector class."""
    def __init__(self):
        self.books = {symbol: OrderBook(symbol) for symbol in config.SYMBOLS}
        self.ws = None
        self.running = False
        self.records_written = 0
        self.db_path = config.DB_PATH
        
        # Ensure database exists
        self._init_db()
    
    def _init_db(self):
        """Initialize database connection and verify table exists."""
        if not self.db_path.exists():
            logger.error(f"Database not found: {self.db_path}")
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        # Verify ml_snapshots table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='ml_snapshots'
        """)
        if not cursor.fetchone():
            logger.error("ml_snapshots table not found!")
            raise RuntimeError("ml_snapshots table not found in database")
        
        conn.close()
        logger.info(f"[OK] Database ready: {self.db_path}")
    
    def write_snapshot(self, symbol: str, bid: float, ask: float, mid: float):
        """Write snapshot to database."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            ts = int(time.time() * 1000)  # milliseconds
            spread_bps = ((ask - bid) / mid) * 10000 if mid else 0
            
            cursor.execute("""
                INSERT INTO ml_snapshots 
                (ts, symbol, exchange, bid, ask, mid, your_offset_bps, 
                 scanner_preset, ml_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts,
                symbol,
                'mexc',
                bid,
                ask,
                mid,
                spread_bps,
                'hedgehog',
                'v0_collection',
                datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            ))
            
            conn.commit()
            conn.close()
            
            self.records_written += 1
            
        except Exception as e:
            logger.error(f"Failed to write snapshot for {symbol}: {e}")
    
    async def handle_message(self, message: str):
        """Handle WebSocket message."""
        try:
            data = json.loads(message)
            
            # MEXC sends: {"c":"spot@public.limit.depth.v3.api@BTCUSDT","d":{"bids":[],"asks":[]}}
            if 'c' in data and 'd' in data:
                channel = data['c']
                # Extract symbol from channel name
                if '@' in channel:
                    parts = channel.split('@')
                    if len(parts) >= 3:
                        symbol = parts[-1]  # Last part is symbol
                        
                        if symbol in self.books:
                            book_data = data['d']
                            self.books[symbol].update(book_data)
                            
        except json.JSONDecodeError:
            pass  # Ignore non-JSON messages (pings, etc.)
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def collect_loop(self):
        """Main collection loop - samples and writes to DB."""
        logger.info("Starting collection loop...")
        
        while self.running:
            try:
                # Sample all books
                for symbol, book in self.books.items():
                    bid = book.get_best_bid()
                    ask = book.get_best_ask()
                    mid = book.get_mid()
                    
                    if bid and ask and mid:
                        self.write_snapshot(symbol, bid, ask, mid)
                        logger.debug(f"{symbol}: bid={bid:.6f}, ask={ask:.6f}, mid={mid:.6f}")
                
                # Log status every 100 records
                if self.records_written % 100 == 0 and self.records_written > 0:
                    logger.info(f"[OK] Records written: {self.records_written}")
                
                await asyncio.sleep(config.COLLECTION_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in collection loop: {e}")
                await asyncio.sleep(1)
    
    async def websocket_loop(self):
        """WebSocket connection loop with auto-reconnect."""
        while self.running:
            try:
                logger.info(f"Connecting to {config.MEXC_WS_URL}...")
                
                async with websockets.connect(config.MEXC_WS_URL) as ws:
                    self.ws = ws
                    logger.info("[OK] WebSocket connected")
                    
                    # Subscribe to order books
                    for symbol in config.SYMBOLS:
                        sub_msg = {
                            "method": "SUBSCRIPTION",
                            "params": [f"spot@public.limit.depth.v3.api@{symbol}"]
                        }
                        await ws.send(json.dumps(sub_msg))
                        logger.info(f"[OK] Subscribed to {symbol}")
                    
                    # Listen for messages
                    async for message in ws:
                        await self.handle_message(message)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket closed, reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"WebSocket error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)
    
    async def run(self):
        """Run the collector."""
        self.running = True
        logger.info("=" * 60)
        logger.info("ML DATA COLLECTOR STARTED")
        logger.info(f"Symbols: {', '.join(config.SYMBOLS)}")
        logger.info(f"Database: {self.db_path}")
        logger.info(f"Interval: {config.COLLECTION_INTERVAL}s")
        logger.info("=" * 60)
        
        # Run both loops concurrently
        await asyncio.gather(
            self.websocket_loop(),
            self.collect_loop()
        )
    
    def stop(self):
        """Stop the collector."""
        logger.info("Stopping collector...")
        self.running = False

async def main():
    """Main entry point."""
    collector = MLCollector()
    
    try:
        await collector.run()
    except KeyboardInterrupt:
        logger.info("Received Ctrl+C, shutting down...")
        collector.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())