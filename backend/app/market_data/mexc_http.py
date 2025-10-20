# app/market_data/mexc_http.py
from __future__ import annotations

import os
import time
import random
import logging
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import httpx

from app.config.settings import settings

# Prometheus metrics (опционально - если используете)
try:
    from prometheus_client import Counter, Histogram, Gauge
    METRICS_AVAILABLE = True
    
    mexc_rest_requests = Counter(
        'mexc_rest_requests_total',
        'Total REST requests to MEXC',
        ['endpoint', 'status']
    )
    
    mexc_rest_latency = Histogram(
        'mexc_rest_latency_seconds',
        'MEXC REST request latency',
        ['endpoint'],
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0]
    )
    
    mexc_rest_timeouts = Counter(
        'mexc_rest_timeouts_total',
        'Total MEXC REST timeouts',
        ['endpoint']
    )
    
    mexc_rest_rate_limits = Counter(
        'mexc_rest_rate_limits_total',
        'Total 429 rate limit hits',
        ['endpoint']
    )
    
    mexc_rest_403_blocks = Counter(
        'mexc_rest_403_blocks_total',
        'Total 403 Forbidden blocks',
        ['endpoint']
    )
    
    mexc_circuit_breaker_trips = Counter(
        'mexc_circuit_breaker_trips_total',
        'Circuit breaker activation count'
    )
    
    mexc_circuit_breaker_state = Gauge(
        'mexc_circuit_breaker_open',
        'Circuit breaker state (1=open, 0=closed)'
    )
    
    mexc_cache_hits = Counter(
        'mexc_cache_hits_total',
        'Cache hits for MEXC endpoints',
        ['cache_type']
    )
    
    mexc_cache_misses = Counter(
        'mexc_cache_misses_total',
        'Cache misses for MEXC endpoints',
        ['cache_type']
    )
    
    mexc_requests_queued = Gauge(
        'mexc_requests_queued',
        'Number of requests waiting in semaphore'
    )
except ImportError:
    METRICS_AVAILABLE = False


logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Circuit breaker для защиты от блокировки MEXC.
    
    Состояния:
    - CLOSED: Нормальная работа
    - OPEN: Блокировка запросов (после N ошибок подряд)
    - HALF_OPEN: Тестовый режим (пропускает 1 запрос для проверки)
    """
    
    def __init__(
        self, 
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 60.0,
        half_open_max_calls: int = 1
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.half_open_max_calls = half_open_max_calls
        
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._half_open_calls = 0
        
        logger.info(
            f"CircuitBreaker initialized: threshold={failure_threshold}, "
            f"timeout={recovery_timeout_sec}s"
        )
    
    @property
    def state(self) -> str:
        """Текущее состояние: CLOSED | OPEN | HALF_OPEN"""
        return self._state
    
    def is_open(self) -> bool:
        """True если circuit breaker открыт (блокирует запросы)"""
        if self._state == "CLOSED":
            return False
        
        if self._state == "OPEN":
            # Проверяем, не пора ли перейти в HALF_OPEN
            if self._last_failure_time:
                elapsed = (datetime.now() - self._last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout_sec:
                    logger.info("Circuit breaker: OPEN → HALF_OPEN (recovery timeout elapsed)")
                    self._state = "HALF_OPEN"
                    self._half_open_calls = 0
                    if METRICS_AVAILABLE:
                        mexc_circuit_breaker_state.set(0)
                    return False
            return True
        
        if self._state == "HALF_OPEN":
            # Пропускаем ограниченное число запросов
            if self._half_open_calls >= self.half_open_max_calls:
                return True
            return False
        
        return False
    
    def record_success(self) -> None:
        """Регистрируем успешный запрос"""
        if self._state == "HALF_OPEN":
            logger.info("Circuit breaker: HALF_OPEN → CLOSED (request succeeded)")
            self._state = "CLOSED"
            if METRICS_AVAILABLE:
                mexc_circuit_breaker_state.set(0)
        
        self._failure_count = 0
        self._half_open_calls = 0
    
    def record_failure(self, is_403: bool = False) -> None:
        """
        Регистрируем неудачный запрос.
        
        Args:
            is_403: True если это 403 Forbidden (блокировка API)
        """
        if self._state == "HALF_OPEN":
            # В режиме HALF_OPEN любая ошибка переводит обратно в OPEN
            logger.warning("Circuit breaker: HALF_OPEN → OPEN (request failed)")
            self._state = "OPEN"
            self._last_failure_time = datetime.now()
            if METRICS_AVAILABLE:
                mexc_circuit_breaker_state.set(1)
                mexc_circuit_breaker_trips.inc()
            return
        
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        
        # 403 ошибки учитываем сильнее (открываем сразу)
        threshold = 1 if is_403 else self.failure_threshold
        
        if self._failure_count >= threshold:
            if self._state == "CLOSED":
                logger.error(
                    f"Circuit breaker: CLOSED → OPEN "
                    f"(failures={self._failure_count}, threshold={threshold})"
                )
                self._state = "OPEN"
                if METRICS_AVAILABLE:
                    mexc_circuit_breaker_state.set(1)
                    mexc_circuit_breaker_trips.inc()
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику для мониторинга"""
        return {
            "state": self._state,
            "failure_count": self._failure_count,
            "last_failure": self._last_failure_time.isoformat() if self._last_failure_time else None,
            "recovery_timeout_sec": self.recovery_timeout_sec,
        }


class MexcHttp:
    """
    Лёгкая HTTP-обёртка для MEXC Spot V3 (публичные эндпоинты), без авторизации.

    ВАЖНО: реализованы таймауты и ретраи с backoff:
      - таймаут запроса: MEXC_REST_TIMEOUT (по умолчанию 10.0с)
      - попыток:        MEXC_REST_ATTEMPTS (по умолчанию 3)
      - базовый backoff: MEXC_REST_BACKOFF_BASE (по умолчанию 1.5с)
      - поддержка Retry-After при 429
      
    НОВОЕ в этой версии:
      - ✅ Rate limiting (max 3 concurrent requests)
      - ✅ Request throttling (250ms delay between requests)
      - ✅ Circuit breaker (stops after 3 consecutive 403s)
      - ✅ Request queue (semaphore-based)
      - Кэш для /api/v3/ticker/24hr (60s TTL)
      - Prometheus метрики (опционально)
      - Улучшенное логирование
      - Метрики cache hit/miss rate
    """

    EXCHANGE_INFO = "/api/v3/exchangeInfo"
    BOOK_TICKER = "/api/v3/ticker/bookTicker"
    TICKER_24H = "/api/v3/ticker/24hr"  # NEW: 24h ticker для всех символов
    DEPTH = "/api/v3/depth"
    TRADES = "/api/v3/trades"
    KLINES = "/api/v3/klines"  # NEW: для candles

    # Дефолтные комиссии для spot:
    DEFAULT_MAKER_FEE = 0.0       # 0%
    DEFAULT_TAKER_FEE = 0.0005    # 0.05%

    def __init__(
        self, 
        base_url: str = "https://api.mexc.com", 
        *, 
        ttl_sec: int = 60,
        ticker_24h_ttl_sec: int = 60,
        max_concurrent_requests: int = 3,  # NEW
        request_delay_ms: int = 250,  # NEW
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._ttl_sec = ttl_sec
        self._ticker_24h_ttl_sec = ticker_24h_ttl_sec
        
        # NEW: Rate limiting
        self._max_concurrent = max_concurrent_requests
        self._request_delay_sec = request_delay_ms / 1000.0
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()
        
        # NEW: Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout_sec=60.0,
            half_open_max_calls=1
        )

        # Настройки таймаутов и ретраев из settings
        self._timeout_s: float = settings.rest_timeout_sec
        self._attempts: int = settings.rest_retry_attempts
        self._backoff_base_s: float = settings.rest_retry_backoff_ms / 1000.0
        self._backoff_max_s: float = settings.rest_backoff_max_sec

        # Порог для логирования медленных запросов
        self._slow_request_threshold_s: float = 5.0

        # HTTP клиент
        transport = httpx.AsyncHTTPTransport(retries=settings.rest_retry_attempts)
        self._client = httpx.AsyncClient(
            http2=False,
            transport=transport,
            timeout=httpx.Timeout(self._timeout_s),
            follow_redirects=True,
            headers={"User-Agent": "liquidity-scanner/1.0"},
        )

        # Кэши
        self._cache_exch: Dict[str, Any] = {}
        self._cache_exch_ts: float = 0.0
        
        self._cache_ticker_24h: List[Dict[str, Any]] = []
        self._cache_ticker_24h_ts: float = 0.0
        
        # fee override per symbol: {"BTCUSDT": (maker, taker)}
        self._symbol_fee_override: Dict[str, Tuple[float, float]] = {}

        logger.info(
            f"MexcHttp initialized: base_url={base_url}, timeout={self._timeout_s}s, "
            f"attempts={self._attempts}, ttl={self._ttl_sec}s, "
            f"max_concurrent={max_concurrent_requests}, delay={request_delay_ms}ms"
        )

    # ---------- Вспомогательное ----------

    def _now(self) -> float:
        return time.time()

    async def _enforce_rate_limit(self) -> None:
        """
        NEW: Принудительная задержка между запросами.
        Гарантирует минимум _request_delay_sec между запросами.
        """
        async with self._request_lock:
            now = self._now()
            elapsed = now - self._last_request_time
            
            if elapsed < self._request_delay_sec:
                sleep_time = self._request_delay_sec - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time*1000:.0f}ms")
                await asyncio.sleep(sleep_time)
            
            self._last_request_time = self._now()

    def _sleep_backoff(self, attempt: int, retry_after_s: Optional[float] = None) -> None:
        """Экспоненциальная задержка с джиттером. Учитывает Retry-After, если передан."""
        if retry_after_s is not None and retry_after_s > 0:
            logger.debug(f"Using Retry-After={retry_after_s}s from MEXC")
            time.sleep(retry_after_s)
            return
        
        base = self._backoff_base_s * (settings.rest_retry_backoff_factor ** max(0, attempt - 1))
        base = min(base, self._backoff_max_s)
        jitter = base * random.uniform(0.7, 1.3)
        logger.debug(f"Backoff sleep: {jitter:.2f}s (attempt {attempt})")
        time.sleep(jitter)

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Универсальный запрос с ретраями, rate limiting и circuit breaker.
        
        NEW: 
        - Проверяет circuit breaker перед запросом
        - Использует semaphore для ограничения concurrent requests
        - Добавляет delay между запросами
        - Регистрирует 403 ошибки в circuit breaker
        """
        url = f"{self.base_url}{path}"
        endpoint = path.split("?")[0]
        
        # NEW: Проверяем circuit breaker
        if self._circuit_breaker.is_open():
            cb_state = self._circuit_breaker.get_stats()
            logger.warning(
                f"Circuit breaker OPEN: blocking request to {endpoint}. "
                f"State: {cb_state['state']}, failures: {cb_state['failure_count']}"
            )
            raise Exception(
                f"Circuit breaker OPEN: MEXC API temporarily unavailable. "
                f"Will retry after {self._circuit_breaker.recovery_timeout_sec}s"
            )
        
        attempts = max(1, self._attempts)
        last_exc: Optional[Exception] = None
        
        start_time = time.time()
        
        # NEW: Acquire semaphore (limit concurrent requests)
        async with self._semaphore:
            if METRICS_AVAILABLE:
                # Count how many requests are waiting
                waiting = self._max_concurrent - self._semaphore._value
                mexc_requests_queued.set(waiting)
            
            # NEW: Enforce rate limit (delay between requests)
            await self._enforce_rate_limit()

            for attempt in range(1, attempts + 1):
                try:
                    if attempt > 1:
                        logger.warning(
                            f"MEXC retry attempt {attempt}/{attempts} for {endpoint}"
                        )
                    
                    resp = await self._client.request(
                        method, url, params=params, timeout=self._timeout_s
                    )
                    
                    # Метрика latency
                    duration = time.time() - start_time
                    if METRICS_AVAILABLE:
                        mexc_rest_latency.labels(endpoint=endpoint).observe(duration)
                    
                    # Логирование медленных запросов
                    if duration > self._slow_request_threshold_s:
                        logger.warning(
                            f"Slow MEXC request: {endpoint} took {duration:.2f}s "
                            f"(threshold={self._slow_request_threshold_s}s)"
                        )
                    
                    # 2xx — успех
                    if 200 <= resp.status_code < 300:
                        if METRICS_AVAILABLE:
                            mexc_rest_requests.labels(endpoint=endpoint, status='success').inc()
                        
                        # NEW: Регистрируем успех в circuit breaker
                        self._circuit_breaker.record_success()
                        
                        return resp.json()

                    # NEW: 403 Forbidden — блокировка API
                    if resp.status_code == 403:
                        if METRICS_AVAILABLE:
                            mexc_rest_403_blocks.labels(endpoint=endpoint).inc()
                        
                        logger.error(
                            f"MEXC 403 Forbidden on {endpoint} - API blocked our requests! "
                            f"Circuit breaker will activate."
                        )
                        
                        # Регистрируем в circuit breaker (откроет его сразу)
                        self._circuit_breaker.record_failure(is_403=True)
                        
                        if METRICS_AVAILABLE:
                            mexc_rest_requests.labels(endpoint=endpoint, status='http_403').inc()
                        
                        # Не ретраим 403 - бесполезно
                        resp.raise_for_status()

                    # 429 — rate limit
                    retry_after_s: Optional[float] = None
                    if resp.status_code == 429:
                        if METRICS_AVAILABLE:
                            mexc_rest_rate_limits.labels(endpoint=endpoint).inc()
                        
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            try:
                                retry_after_s = float(ra)
                            except Exception:
                                retry_after_s = None
                        
                        logger.warning(
                            f"MEXC rate limit (429) on {endpoint}, "
                            f"retry_after={retry_after_s}s, attempt {attempt}/{attempts}"
                        )
                        
                        # Регистрируем как failure (но не 403, поэтому не откроет сразу)
                        self._circuit_breaker.record_failure(is_403=False)

                    # 5xx или 429 — ретраим
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                        if METRICS_AVAILABLE:
                            mexc_rest_requests.labels(
                                endpoint=endpoint, 
                                status=f'http_{resp.status_code}'
                            ).inc()
                        self._sleep_backoff(attempt, retry_after_s)
                        continue

                    # Другие ошибки — fail
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(
                            endpoint=endpoint, 
                            status=f'http_{resp.status_code}'
                        ).inc()
                    
                    logger.error(
                        f"MEXC request failed with status {resp.status_code}: {endpoint}"
                    )
                    
                    # Регистрируем failure
                    self._circuit_breaker.record_failure(is_403=False)
                    
                    resp.raise_for_status()
                    return resp.json()

                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    
                    if METRICS_AVAILABLE:
                        mexc_rest_timeouts.labels(endpoint=endpoint).inc()
                    
                    logger.error(
                        f"MEXC timeout/transport error on {endpoint} "
                        f"(attempt {attempt}/{attempts}): {e}"
                    )
                    
                    # Регистрируем failure
                    self._circuit_breaker.record_failure(is_403=False)
                    
                    if attempt < attempts:
                        self._sleep_backoff(attempt)
                        continue
                    
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(endpoint=endpoint, status='timeout').inc()
                    
                    raise Exception(
                        f"MEXC request failed after {attempts} retries "
                        f"(timeout/transport): {e}"
                    ) from e

                except httpx.HTTPStatusError as e:
                    last_exc = e
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(endpoint=endpoint, status='http_error').inc()
                    
                    # Регистрируем failure
                    self._circuit_breaker.record_failure(
                        is_403=(e.response.status_code == 403)
                    )
                    
                    logger.error(f"MEXC HTTP status error: {e}")
                    break

                except Exception as e:
                    last_exc = e
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(endpoint=endpoint, status='error').inc()
                    
                    # Регистрируем failure
                    self._circuit_breaker.record_failure(is_403=False)
                    
                    logger.error(f"MEXC unexpected error: {e}")
                    break

        # Если вышли из цикла без return
        if last_exc:
            raise Exception(f"Unexpected error in MEXC request to {endpoint}: {last_exc}")
        raise Exception(f"Unexpected empty error in MEXC request to {endpoint}")

    async def _cached_exchange_info(self) -> Dict[str, Any]:
        now = self._now()
        if not self._cache_exch or (now - self._cache_exch_ts) > self._ttl_sec:
            if METRICS_AVAILABLE:
                mexc_cache_misses.labels(cache_type='exchange_info').inc()
            
            logger.debug("Fetching fresh exchangeInfo from MEXC")
            data = await self._request_with_retry("GET", self.EXCHANGE_INFO)
            self._parse_fees_from_exchange_info(data)
            self._cache_exch = data
            self._cache_exch_ts = now
            logger.info(f"Cached exchangeInfo: {len(data.get('symbols', []))} symbols")
        else:
            if METRICS_AVAILABLE:
                mexc_cache_hits.labels(cache_type='exchange_info').inc()
            logger.debug("Using cached exchangeInfo")
        
        return self._cache_exch

    def _parse_fees_from_exchange_info(self, data: Dict[str, Any]) -> None:
        """
        Если в exchangeInfo найдутся расширенные filters с комиссиями — сохраним их.
        """
        try:
            symbols = data.get("symbols") or []
            fee_count = 0
            
            for s in symbols:
                symbol = s.get("symbol")
                if not symbol:
                    continue
                maker_fee: Optional[float] = None
                taker_fee: Optional[float] = None

                if "makerCommission" in s:
                    try:
                        maker_fee = float(s["makerCommission"])
                    except Exception:
                        pass
                if "takerCommission" in s:
                    try:
                        taker_fee = float(s["takerCommission"])
                    except Exception:
                        pass

                for f in (s.get("filters") or []):
                    ftype = (f.get("filterType") or "").upper()
                    if ftype == "FEES":
                        if "maker" in f:
                            try:
                                maker_fee = float(f["maker"])
                            except Exception:
                                pass
                        if "taker" in f:
                            try:
                                taker_fee = float(f["taker"])
                            except Exception:
                                pass

                if maker_fee is not None or taker_fee is not None:
                    self._symbol_fee_override[symbol] = (
                        maker_fee if maker_fee is not None else self.DEFAULT_MAKER_FEE,
                        taker_fee if taker_fee is not None else self.DEFAULT_TAKER_FEE,
                    )
                    fee_count += 1
            
            logger.info(f"Parsed fees for {fee_count} symbols from exchangeInfo")
        except Exception as e:
            logger.warning(f"Failed to parse fees from exchangeInfo: {e}")

    def _fees_for_symbol(self, symbol: str) -> Tuple[float, float, bool]:
        """
        Возвращает (maker_fee, taker_fee, zero_fee_flag) для символа.
        """
        maker, taker = self._symbol_fee_override.get(
            symbol.upper(),
            (self.DEFAULT_MAKER_FEE, self.DEFAULT_TAKER_FEE),
        )
        zero = (maker == 0.0)
        return maker, taker, zero

    async def close(self) -> None:
        """Закрывает HTTP-клиент."""
        logger.info("Closing MexcHttp client")
        await self._client.aclose()

    # ---------- Публичные методы ----------

    async def fetch_symbols(self) -> List[Dict[str, Any]]:
        """Возвращает список символов c метаданными и комиссиями."""
        data = await self._cached_exchange_info()
        out: List[Dict[str, Any]] = []

        for s in data.get("symbols", []):
            symbol = s.get("symbol")
            if not symbol:
                continue
            base = s.get("baseAsset")
            quote = s.get("quoteAsset")
            status = s.get("status")
            perms = s.get("permissions") or s.get("permission") or []
            if isinstance(perms, str):
                perms = [perms]

            mk, tk, zero = self._fees_for_symbol(symbol)

            out.append({
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "status": status,
                "permissions": perms,
                "maker_fee": mk,
                "taker_fee": tk,
                "zero_fee": zero,
            })
        return out

    async def fetch_24h_tickers(self, quote: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        GET /api/v3/ticker/24hr (все символы с 24h метриками).
        Кэш: 60 секунд.
        """
        now = self._now()
        
        if (
            self._cache_ticker_24h 
            and (now - self._cache_ticker_24h_ts) < self._ticker_24h_ttl_sec
        ):
            if METRICS_AVAILABLE:
                mexc_cache_hits.labels(cache_type='ticker_24h').inc()
            logger.debug("Using cached 24h tickers")
            data = self._cache_ticker_24h
        else:
            if METRICS_AVAILABLE:
                mexc_cache_misses.labels(cache_type='ticker_24h').inc()
            
            logger.debug("Fetching fresh 24h tickers from MEXC")
            raw_data = await self._request_with_retry("GET", self.TICKER_24H)
            
            normalized: List[Dict[str, Any]] = []
            for t in raw_data:
                symbol = t.get("symbol")
                if not symbol:
                    continue
                
                mk, tk, zero = self._fees_for_symbol(symbol)
                
                normalized.append({
                    "symbol": symbol,
                    "last": float(t.get("lastPrice", 0.0)),
                    "volume": float(t.get("volume", 0.0)),
                    "quoteVolume": float(t.get("quoteVolume", 0.0)),
                    "priceChangePercent": float(t.get("priceChangePercent", 0.0)),
                    "high": float(t.get("highPrice", 0.0)),
                    "low": float(t.get("lowPrice", 0.0)),
                    "maker_fee": mk,
                    "taker_fee": tk,
                    "zero_fee": zero,
                    "raw": t,
                })
            
            self._cache_ticker_24h = normalized
            self._cache_ticker_24h_ts = now
            logger.info(f"Cached 24h tickers: {len(normalized)} symbols")
            data = normalized
        
        if quote:
            quote_upper = quote.upper()
            data = [t for t in data if t["symbol"].endswith(quote_upper)]
            logger.debug(f"Filtered to {len(data)} symbols with quote={quote}")
        
        return data

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """GET /api/v3/ticker/bookTicker?symbol=BTCUSDT"""
        params = {"symbol": symbol.upper()}
        data = await self._request_with_retry("GET", self.BOOK_TICKER, params=params)

        bid = float(data.get("bidPrice", 0.0))
        ask = float(data.get("askPrice", 0.0))
        last: Optional[float] = None
        if "lastPrice" in data:
            try:
                last = float(data["lastPrice"])
            except Exception:
                last = None
        if last is None and bid and ask:
            last = (bid + ask) / 2.0

        mk, tk, zero = self._fees_for_symbol(symbol)
        return {
            "symbol": symbol.upper(),
            "bid": bid,
            "ask": ask,
            "last": last,
            "maker_fee": mk,
            "taker_fee": tk,
            "zero_fee": zero,
            "raw": data,
        }

    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> Dict[str, Any]:
        """GET /api/v3/depth?symbol=BTCUSDT&limit=50"""
        params = {"symbol": symbol.upper(), "limit": limit}
        data = await self._request_with_retry("GET", self.DEPTH, params=params)

        bids = data.get("bids") or []
        asks = data.get("asks") or []
        nbids = [(float(p), float(q)) for p, q in bids]
        nasks = [(float(p), float(q)) for p, q in asks]

        mk, tk, zero = self._fees_for_symbol(symbol)
        return {
            "symbol": symbol.upper(),
            "bids": nbids,
            "asks": nasks,
            "maker_fee": mk,
            "taker_fee": tk,
            "zero_fee": zero,
            "raw": data,
        }

    async def fetch_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """GET /api/v3/trades?symbol=BTCUSDT&limit=50"""
        params = {"symbol": symbol.upper(), "limit": limit}
        data = await self._request_with_retry("GET", self.TRADES, params=params)
        out: List[Dict[str, Any]] = []

        for t in data:
            price = float(t.get("price", 0.0))
            qty = float(t.get("qty", 0.0))
            ibm = t.get("isBuyerMaker")
            if isinstance(ibm, str):
                is_bm = ibm.lower() == "true"
            else:
                is_bm = bool(ibm)
            ts = t.get("time") or t.get("T") or 0

            out.append({
                "price": price,
                "qty": qty,
                "isBuyerMaker": is_bm,
                "ts": int(ts),
                "raw": t,
            })
        return out

    async def fetch_klines(
        self, 
        symbol: str, 
        interval: str = "1m", 
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=100"""
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        data = await self._request_with_retry("GET", self.KLINES, params=params)
        
        out: List[Dict[str, Any]] = []
        for k in data:
            if len(k) < 7:
                continue
            
            out.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "raw": k,
            })
        
        return out

    def get_fee_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Возвращает комиссии для символа или дефолты."""
        if symbol:
            mk, tk, zero = self._fees_for_symbol(symbol.upper())
            return {
                "symbol": symbol.upper(), 
                "maker_fee": mk, 
                "taker_fee": tk, 
                "zero_fee": zero
            }
        return {
            "maker_fee": self.DEFAULT_MAKER_FEE,
            "taker_fee": self.DEFAULT_TAKER_FEE,
            "zero_fee": self.DEFAULT_MAKER_FEE == 0.0,
        }
    
    # ---------- Диагностика / метрики ----------
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кэшей для мониторинга."""
        now = self._now()
        return {
            "exchange_info": {
                "cached": bool(self._cache_exch),
                "age_sec": now - self._cache_exch_ts if self._cache_exch else None,
                "ttl_sec": self._ttl_sec,
                "symbols_count": len(self._cache_exch.get("symbols", [])) if self._cache_exch else 0,
            },
            "ticker_24h": {
                "cached": bool(self._cache_ticker_24h),
                "age_sec": now - self._cache_ticker_24h_ts if self._cache_ticker_24h else None,
                "ttl_sec": self._ticker_24h_ttl_sec,
                "symbols_count": len(self._cache_ticker_24h),
            },
            "fee_overrides": len(self._symbol_fee_override),
            "circuit_breaker": self._circuit_breaker.get_stats(),  # NEW
        }
    
    def invalidate_cache(self, cache_type: Optional[str] = None) -> None:
        """Принудительно сбрасывает кэш."""
        if cache_type is None or cache_type == "exchange_info":
            self._cache_exch = {}
            self._cache_exch_ts = 0.0
            logger.info("Invalidated exchange_info cache")
        
        if cache_type is None or cache_type == "ticker_24h":
            self._cache_ticker_24h = []
            self._cache_ticker_24h_ts = 0.0
            logger.info("Invalidated ticker_24h cache")
    
    def reset_circuit_breaker(self) -> None:
        """
        NEW: Принудительно сбрасывает circuit breaker (для тестирования).
        """
        self._circuit_breaker._state = "CLOSED"
        self._circuit_breaker._failure_count = 0
        self._circuit_breaker._half_open_calls = 0
        if METRICS_AVAILABLE:
            mexc_circuit_breaker_state.set(0)
        logger.warning("Circuit breaker manually reset to CLOSED")