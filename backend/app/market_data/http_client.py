# app/market_data/http_client.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
import httpx

from app.config.settings import settings

try:
    from prometheus_client import Counter, Histogram
except ImportError:
    Counter = Histogram = None

logger = logging.getLogger(__name__)

if Counter and Histogram:
    http_requests_total = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["provider", "symbol", "status"]
    )
    http_request_duration_seconds = Histogram(
        "http_request_duration_seconds",
        "HTTP request latency",
        ["provider", "symbol"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
    )
    http_errors_total = Counter(
        "http_errors_total",
        "Total HTTP errors",
        ["provider", "symbol", "error_type"]
    )
else:
    http_requests_total = http_request_duration_seconds = http_errors_total = None

# --- Базы MEXC ---
MEXC_V3_BASE = "https://api.mexc.com/api/v3"
MEXC_V2_BASE = "https://www.mexc.com/open/api/v2"
# --- База Binance ---
BINANCE_BASE = "https://api.binance.com/api/v3"


def _mk_client(verify: bool, proxy_url: Optional[str], user_agent: str) -> httpx.AsyncClient:
    """
    Клиент для httpx>=0.27:
    - proxy передаём через параметр `proxies=...` в AsyncClient (НЕ в transport)
    - http2=False
    - trust_env=True (подхват системных настроек/сертификатов)
    - retries/timeout из settings
    """
    transport = httpx.AsyncHTTPTransport(retries=settings.rest_retry_attempts)  # ⬅️ ИЗМЕНИТЬ
    return httpx.AsyncClient(
        http2=False,
        transport=transport,
        verify=verify,
        trust_env=True,
        timeout=httpx.Timeout(settings.rest_timeout_sec),
        headers={"User-Agent": user_agent},
        proxies=proxy_url or None,
    )


class MEXCHTTPClient:
    """
    HTTP-поллер маркет-данных с авто-fallback:
      1) MEXC v3 (/ping), затем v2 (/common/timestamp).
      2) Если оба недоступны — Binance (/ping).
    Нормализует в кортеж (last, bid, ask) и отдаёт в on_update(symbol, last, bid, ask).

    Использование:
      client = MEXCHTTPClient(settings.symbols, on_update=callback)
      await client.start()
      ...
      await client.stop()
    """

    def __init__(
        self,
        symbols: List[str],
        on_update: Optional[Callable[[str, Optional[float], Optional[float], Optional[float]], Awaitable[None]]] = None,
        interval: Optional[float] = None,
        depth_limit: Optional[int] = None,
        proxy_url: Optional[str] = None,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.interval = interval if interval is not None else settings.poll_interval_sec
        self.depth_limit = depth_limit if depth_limit is not None else settings.depth_limit

        self.mexc_v3_base = (settings.rest_base_url or MEXC_V3_BASE).rstrip("/")
        self.mexc_v2_base = MEXC_V2_BASE
        self.binance_base = BINANCE_BASE

        self.proxy_url: Optional[str] = proxy_url if proxy_url is not None else (settings.proxy_url or None)
        self.ua = "mexc-trade-bot/0.1"

        self.client: Optional[httpx.AsyncClient] = None
        self.mode: Optional[str] = None  # "mexc_v3" | "mexc_v2" | "binance"

        # управление задачами
        self._tasks: List[asyncio.Task] = []
        self._stop = asyncio.Event()

        # коллбек наверх (в твой сервисный слой)
        self._on_update = on_update

        # Счётчик символов, недоступных на текущем провайдере
        self._unavailable_symbols: set[str] = set()

        self._backoff_max = settings.rest_backoff_max_sec 

    # ---------- Detect provider ----------
    async def detect_provider(self) -> None:
        # 1) MEXC v3
        c = _mk_client(verify=True, proxy_url=self.proxy_url, user_agent=self.ua)
        try:
            r = await c.get(f"{self.mexc_v3_base}/ping")
            r.raise_for_status()
            self.client, self.mode = c, "mexc_v3"
            logger.info(f"✅ Using MEXC v3: {self.mexc_v3_base}")
            return
        except Exception as e:
            logger.warning(f"❌ MEXC v3 ping fail: {e}")
            await c.aclose()

        # 2) MEXC v2
        c = _mk_client(verify=True, proxy_url=self.proxy_url, user_agent=self.ua)
        try:
            r = await c.get(f"{self.mexc_v2_base}/common/timestamp")
            if r.status_code == 200:
                self.client, self.mode = c, "mexc_v2"
                logger.info(f"✅ Using MEXC v2: {self.mexc_v2_base}")
                return
            logger.warning(f"❌ MEXC v2 responded {r.status_code}: {r.text[:120]}")
            await c.aclose()
        except Exception as e:
            logger.warning(f"❌ MEXC v2 ping fail: {e}")
            await c.aclose()

        # 3) Binance
        c = _mk_client(verify=True, proxy_url=self.proxy_url, user_agent=self.ua)
        try:
            r = await c.get(f"{self.binance_base}/ping")
            r.raise_for_status()
            self.client, self.mode = c, "binance"
            logger.info(f"✅ Using Binance: {self.binance_base}")
            return
        except Exception as e:
            logger.warning(f"❌ Binance ping fail: {e}")
            await c.aclose()

        # 4) Диагностика (verify=False) — опционально
        for base, label in ((self.mexc_v3_base, "MEXC v3"), (self.mexc_v2_base, "MEXC v2")):
            c = _mk_client(verify=False, proxy_url=self.proxy_url, user_agent=self.ua)
            try:
                path = "/ping" if base.endswith("/api/v3") else "/common/timestamp"
                r = await c.get(f"{base}{path}")
                r.raise_for_status()
                self.client, self.mode = c, "mexc_v3" if base.endswith("/api/v3") else "mexc_v2"
                logger.warning(f"⚠️ Using {label} WITHOUT TLS verify: {base}")
                return
            except Exception as e:
                logger.warning(f"❌ {label} (verify=False) ping fail: {e}")
                await c.aclose()

        raise RuntimeError("❌ Нет доступного провайдера (MEXC v3/v2 и Binance недоступны). Проверь прокси/VPN/фаервол.")

    # ---------- API calls ----------
    async def mexc_v3_ticker(self, symbol: str) -> Dict[str, Any]:
        assert self.client
        url = f"{self.mexc_v3_base}/ticker/24hr"
        r = await self.client.get(url, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()

    async def mexc_v3_depth(self, symbol: str, limit: int) -> Dict[str, Any]:
        assert self.client
        url = f"{self.mexc_v3_base}/depth"
        r = await self.client.get(url, params={"symbol": symbol, "limit": limit})
        r.raise_for_status()
        return r.json()

    async def mexc_v2_ticker(self, symbol_us: str) -> Dict[str, Any]:
        assert self.client
        url = f"{self.mexc_v2_base}/market/ticker"
        r = await self.client.get(url, params={"symbol": symbol_us})
        r.raise_for_status()
        return r.json()

    async def mexc_v2_depth(self, symbol_us: str, depth: int) -> Dict[str, Any]:
        assert self.client
        url = f"{self.mexc_v2_base}/market/depth"
        r = await self.client.get(url, params={"symbol": symbol_us, "depth": depth})
        r.raise_for_status()
        return r.json()

    async def binance_book_ticker(self, symbol: str) -> Dict[str, Any]:
        assert self.client
        url = f"{self.binance_base}/ticker/bookTicker"
        r = await self.client.get(url, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()

    async def binance_24hr(self, symbol: str) -> Dict[str, Any]:
        assert self.client
        url = f"{self.binance_base}/ticker/24hr"
        r = await self.client.get(url, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()

    # ---------- normalize ----------
    @staticmethod
    def normalize_from_mexc_v3(ticker: Dict[str, Any], depth: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        last = float(ticker.get("lastPrice")) if ticker.get("lastPrice") else None
        bid  = float(ticker.get("bidPrice"))  if ticker.get("bidPrice")  else None
        ask  = float(ticker.get("askPrice"))  if ticker.get("askPrice")  else None
        if bid is None:
            try: bid = float(depth["bids"][0][0])
            except Exception: pass
        if ask is None:
            try: ask = float(depth["asks"][0][0])
            except Exception: pass
        return last, bid, ask

    @staticmethod
    def normalize_from_mexc_v2(ticker: Dict[str, Any], depth: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        data = ticker.get("data") or {}
        last = data.get("last") or data.get("lastPrice")
        bid  = data.get("bid")  or data.get("bidPrice")
        ask  = data.get("ask")  or data.get("askPrice")
        last = float(last) if last is not None else None
        bid  = float(bid)  if bid  is not None else None
        ask  = float(ask)  if ask  is not None else None
        ddata = depth.get("data") or {}
        if bid is None:
            try: bid = float(ddata["bids"][0][0])
            except Exception: pass
        if ask is None:
            try: ask = float(ddata["asks"][0][0])
            except Exception: pass
        return last, bid, ask

    @staticmethod
    def normalize_from_binance(book: Dict[str, Any], t24: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        try:
            bid = float(book.get("bidPrice")) if book.get("bidPrice") else None
            ask = float(book.get("askPrice")) if book.get("askPrice") else None
        except Exception:
            bid = ask = None
        last = None
        try:
            last_val = t24.get("lastPrice")
            last = float(last_val) if last_val is not None else None
        except Exception:
            pass
        return last, bid, ask

    # ---------- polling per symbol ----------
    async def _poll_symbol(self, symbol: str) -> None:
        assert self.client and self.mode
        retry_delay = self.interval
        while not self._stop.is_set():
            start_time = time.time()  
            status_code = "success"   
            error_type = None       
            
            try:
                if self.mode == "mexc_v3":
                    t, d = await asyncio.gather(
                        self.mexc_v3_ticker(symbol),
                        self.mexc_v3_depth(symbol, self.depth_limit),
                    )
                    last, bid, ask = self.normalize_from_mexc_v3(t, d)
                elif self.mode == "mexc_v2":
                    s2 = symbol.replace("USDT", "_USDT")
                    t, d = await asyncio.gather(
                        self.mexc_v2_ticker(s2),
                        self.mexc_v2_depth(s2, self.depth_limit),
                    )
                    last, bid, ask = self.normalize_from_mexc_v2(t, d)
                else:  # binance
                    try:
                        book, t24 = await asyncio.gather(
                            self.binance_book_ticker(symbol),
                            self.binance_24hr(symbol),
                        )
                        last, bid, ask = self.normalize_from_binance(book, t24)
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 400:
                            self._unavailable_symbols.add(symbol)
                            logger.warning(f"[{symbol}] ⚠️ Нет в Binance (пропуск). Unavailable: {len(self._unavailable_symbols)}/{len(self.symbols)}")
                            
                            status_code = "404"  # ⬅️ ДОБАВИТЬ
                            error_type = "symbol_not_found"  # ⬅️ ДОБАВИТЬ
                            
                            # Если ВСЕ символы недоступны — останавливаем поллинг
                            if len(self._unavailable_symbols) >= len(self.symbols):
                                logger.error(f"❌ Все {len(self.symbols)} символов недоступны на {self.mode}. Останавливаем поллинг.")
                                return
                            
                            await asyncio.sleep(self.interval)
                            continue
                        raise

                retry_delay = self.interval  # reset backoff on success
                self._unavailable_symbols.discard(symbol)

                # уведомление вверх
                if self._on_update:
                    try:
                        await self._on_update(symbol, last, bid, ask)
                    except Exception as cb_err:
                        logger.error(f"[{symbol}] on_update error: {cb_err}")

                if http_request_duration_seconds:
                    duration = time.time() - start_time
                    http_request_duration_seconds.labels(provider=self.mode, symbol=symbol).observe(duration)
                if http_requests_total:
                    http_requests_total.labels(provider=self.mode, symbol=symbol, status=status_code).inc()

                # лог для диагностики
                if bid is not None and ask is not None:
                    spread = ask - bid
                    logger.debug(f"[{symbol}] last={last} bid={bid} ask={ask} spread={spread}")
                else:
                    logger.debug(f"[{symbol}] last={last} bid={bid} ask={ask}")

            except httpx.HTTPStatusError as e:
                logger.error(f"[{symbol}] HTTP {e.response.status_code}: {e.response.text[:200]}")
                status_code = str(e.response.status_code)  # ⬅️ ДОБАВИТЬ
                error_type = f"http_{e.response.status_code}"  # ⬅️ ДОБАВИТЬ
                retry_delay = min(retry_delay * settings.rest_retry_backoff_factor, self._backoff_max)
            except Exception as e:
                logger.error(f"[{symbol}] Error: {e}")
                status_code = "error"  # ⬅️ ДОБАВИТЬ
                error_type = type(e).__name__  # ⬅️ ДОБАВИТЬ
                retry_delay = min(retry_delay * settings.rest_retry_backoff_factor, self._backoff_max)
            
            # ⬇️ ДОБАВИТЬ ЭТИ 6 СТРОК (сразу после обоих except блоков):
            finally:
                # Record metrics даже при ошибках
                if error_type and http_errors_total:
                    http_errors_total.labels(provider=self.mode, symbol=symbol, error_type=error_type).inc()
                if http_requests_total and status_code != "success":
                    http_requests_total.labels(provider=self.mode, symbol=symbol, status=status_code).inc() 

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=retry_delay)  # ⬅️ ИЗМЕНИТЬ self.interval → retry_delay
            except asyncio.TimeoutError:
                pass  # normal timeout, continue polling

    # ---------- lifecycle ----------
    async def start(self) -> None:
        if not self.client or not self.mode:
            await self.detect_provider()
        self._stop.clear()
        # запускаем отдельную задачу на каждый символ
        self._tasks = [asyncio.create_task(self._poll_symbol(sym)) for sym in self.symbols]

    async def stop(self) -> None:
        self._stop.set()
        # аккуратно закрываем задачи и HTTP клиент
        for t in self._tasks:
            try:
                t.cancel()
            except Exception:
                pass
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._unavailable_symbols:
            logger.warning(f"⚠️ Stopped with {len(self._unavailable_symbols)} unavailable symbols: {self._unavailable_symbols}")
        self._unavailable_symbols.clear()
        if self.client:
            try:
                await self.client.aclose()
            except Exception:
                pass
            self.client = None
            self.mode = None


# Debug launcher
if __name__ == "__main__":
    async def _dbg_update(symbol: str, last, bid, ask):
        logger.info(f"UPDATE {symbol} → last={last} bid={bid} ask={ask}")

    client = MEXCHTTPClient(settings.symbols, on_update=_dbg_update)
    asyncio.run(client.start())
