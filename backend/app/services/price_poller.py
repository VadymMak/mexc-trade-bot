"""
REST-based price poller as fallback/primary data source.
Polls MEXC REST API every few seconds and caches prices.
"""
import asyncio
import time
from typing import Dict, Optional
import httpx


class PricePoller:
    """Polls prices via REST API and caches them."""
    
    def __init__(self, interval_sec: float = 2.5):
        self.interval_sec = interval_sec
        self.prices: Dict[str, dict] = {}  # symbol -> {bid, ask, mid, timestamp}
        self.running = False
        self._task: Optional[asyncio.Task] = None
        
    async def start(self, symbols: list[str]):
        """Start polling for given symbols."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._poll_loop(symbols))
        print(f"✅ PricePoller started for {len(symbols)} symbols")
        
    async def stop(self):
        """Stop polling."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("⏹️ PricePoller stopped")
        
    async def _poll_loop(self, symbols: list[str]):
        """Main polling loop."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            while self.running:
                try:
                    await self._fetch_prices(client, symbols)
                    await asyncio.sleep(self.interval_sec)
                except Exception as e:
                    print(f"⚠️ PricePoller error: {e}")
                    await asyncio.sleep(self.interval_sec)
                    
    async def _fetch_prices(self, client: httpx.AsyncClient, symbols: list[str]):
        """Fetch prices for all symbols in one request."""
        try:
            # MEXC ticker endpoint - gets all tickers at once
            resp = await client.get("https://api.mexc.com/api/v3/ticker/bookTicker")
            resp.raise_for_status()
            data = resp.json()
            
            # Update cache
            now = time.time()
            for item in data:
                symbol = item.get("symbol", "").upper()
                if symbol in [s.upper() for s in symbols]:
                    bid = float(item.get("bidPrice", 0))
                    ask = float(item.get("askPrice", 0))
                    mid = (bid + ask) / 2 if bid and ask else 0
                    
                    self.prices[symbol] = {
                        "bid": bid,
                        "ask": ask,
                        "mid": mid,
                        "timestamp": now
                    }
        except Exception as e:
            print(f"⚠️ Failed to fetch prices: {e}")
            
    def get_price(self, symbol: str) -> Optional[dict]:
        """Get cached price for a symbol."""
        return self.prices.get(symbol.upper())
        
    def get_mid(self, symbol: str) -> Optional[float]:
        """Get mid price for a symbol."""
        price = self.get_price(symbol)
        return price["mid"] if price else None


# Global instance
_poller = PricePoller()


def get_poller() -> PricePoller:
    """Get the global price poller instance."""
    return _poller
