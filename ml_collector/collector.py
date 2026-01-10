# Создать исправленный файл
"""
ML Data Collector - Scanner API version with imbalance tracking
"""
import asyncio
import aiohttp
import sqlite3
import time
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import config

# UTF-8 encoding for Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('collector.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ScannerCollector:
    """Scanner API collector with full market data"""
    def __init__(self):
        self.symbols = config.SYMBOLS
        self.db_path = config.DB_PATH
        self.interval = config.COLLECTION_INTERVAL
        self.scanner_url = config.SCANNER_BASE_URL
        self.timeout = config.SCANNER_TIMEOUT
        self.records_written = 0
        self.errors = 0
        self.running = False
        
        self._init_db()
    
    def _init_db(self):
        """Initialize database (table should already exist)"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='ml_snapshots'
            """)
            
            if cursor.fetchone():
                logger.info(f"[OK] Database ready: {self.db_path}")
            else:
                logger.error(f"[ERROR] Table ml_snapshots not found in {self.db_path}")
                logger.error("[ERROR] Run backend first to create schema")
                sys.exit(1)
            
            conn.close()
            
        except Exception as e:
            logger.error(f"[ERROR] Database init failed: {e}")
            sys.exit(1)
    
    def write_snapshot(self, symbol: str, data: dict):
        """Write snapshot to database"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Use timezone-aware datetime
            now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("""
                INSERT INTO ml_snapshots (
                    ts, symbol, exchange,
                    bid, ask, mid,
                    spread_bps, eff_spread_bps_maker,
                    depth5_bid_usd, depth5_ask_usd,
                    depth10_bid_usd, depth10_ask_usd,
                    imbalance,
                    trades_per_min, usd_per_min, median_trade_usd,
                    atr1m_pct, grinder_ratio, pullback_median_retrace,
                    scanner_preset, ml_version, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(time.time() * 1000),
                symbol,
                'mexc',
                data.get('bid', 0),
                data.get('ask', 0),
                data.get('mid', 0),
                data.get('spread_bps', 0),
                data.get('eff_spread_bps_maker', 0),
                data.get('depth5_bid_usd', 0),
                data.get('depth5_ask_usd', 0),
                data.get('depth10_bid_usd', 0),
                data.get('depth10_ask_usd', 0),
                data.get('imbalance', 0.5),
                data.get('tpm', 0),
                data.get('usdpm', 0),
                data.get('median_trade_usd', 0),
                data.get('atr1m_pct', 0),
                data.get('grinder_ratio', 0),
                data.get('pullback_median_retrace', 0),
                'hedgehog',
                'v1_scanner',
                now_utc
            ))
            
            conn.commit()
            conn.close()
            
            self.records_written += 1
            
        except Exception as e:
            logger.error(f"Write error for {symbol}: {e}")
            self.errors += 1
    
    async def fetch_scanner_data(self, session: aiohttp.ClientSession, symbol: str):
        """Fetch from scanner (includes full market data)"""
        try:
            # ✅ ИСПРАВЛЕНО: Добавлен fetch_candles=True
            params = {'symbols': symbol, 'limit': 1, 'fetch_candles': 'true'}
            
            async with session.get(
                self.scanner_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    if data and len(data) > 0:
                        row = data[0]
                        
                        # Validate critical fields
                        bid = float(row.get('bid', 0))
                        ask = float(row.get('ask', 0))
                        
                        if bid > 0 and ask > 0:
                            self.write_snapshot(symbol, row)
                            return True
                        else:
                            logger.warning(f"{symbol}: Invalid prices (bid={bid}, ask={ask})")
                    else:
                        logger.warning(f"{symbol}: Empty response")
                elif resp.status == 502:
                    logger.warning(f"{symbol}: Backend unavailable (502)")
                else:
                    logger.warning(f"{symbol}: HTTP {resp.status}")
                    
        except asyncio.TimeoutError:
            logger.warning(f"{symbol}: Timeout (>{self.timeout}s)")
            self.errors += 1
        except Exception as e:
            logger.error(f"{symbol}: {e}")
            self.errors += 1
        
        return False
    
    async def collect_cycle(self, session: aiohttp.ClientSession):
        """One collection cycle"""
        tasks = [self.fetch_scanner_data(session, symbol) for symbol in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success = sum(1 for r in results if r is True)
        return success
    
    async def run(self):
        """Main loop"""
        self.running = True
        
        logger.info("=" * 60)
        logger.info("ML DATA COLLECTOR (SCANNER - TOP 5 SYMBOLS)")
        logger.info(f"Symbols: {', '.join(self.symbols)}")
        logger.info(f"Database: {self.db_path}")
        logger.info(f"Interval: {self.interval}s")
        logger.info(f"Timeout: {self.timeout}s")
        logger.info(f"Scanner: {self.scanner_url}")
        logger.info("=" * 60)
        
        # Check backend health
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:8000/api/healthz",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        logger.info("[OK] Backend is running")
                    else:
                        logger.warning(f"[WARN] Backend health check failed: {resp.status}")
        except Exception as e:
            logger.error(f"[ERROR] Cannot reach backend: {e}")
            logger.error("[ERROR] Start backend first: python -m uvicorn app.main:app --reload")
            return
        
        async with aiohttp.ClientSession() as session:
            cycle = 0
            consecutive_failures = 0
            
            while self.running:
                try:
                    cycle += 1
                    success = await self.collect_cycle(session)
                    
                    if success == 0:
                        consecutive_failures += 1
                        logger.warning(f"[WARN] Cycle {cycle}: 0/{len(self.symbols)} successful (failures: {consecutive_failures})")
                        
                        if consecutive_failures >= 5:
                            logger.error("[ERROR] Too many consecutive failures, check backend!")
                            logger.error("[ERROR] Run: curl http://localhost:8000/api/healthz")
                    else:
                        consecutive_failures = 0
                    
                    if cycle % 10 == 0:
                        success_rate = (self.records_written / (cycle * len(self.symbols))) * 100
                        logger.info(
                            f"[CYCLE {cycle}] Success: {success}/{len(self.symbols)} | "
                            f"Total: {self.records_written} | "
                            f"Errors: {self.errors} | "
                            f"Rate: {success_rate:.1f}%"
                        )
                    
                    await asyncio.sleep(self.interval)
                    
                except Exception as e:
                    logger.error(f"Cycle {cycle} error: {e}")
                    await asyncio.sleep(self.interval)
    
    def stop(self):
        self.running = False
        logger.info(f"[STOP] Collected {self.records_written} snapshots with {self.errors} errors")

async def main():
    collector = ScannerCollector()
    
    try:
        await collector.run()
    except KeyboardInterrupt:
        logger.info("\n[STOP] Interrupted by user")
        collector.stop()

if __name__ == "__main__":
    asyncio.run(main())
