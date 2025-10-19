# app/market_data/mexc_http.py
from __future__ import annotations

import os
import time
import random
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config.settings import settings

# Prometheus metrics (опционально - если используете)
try:
    from prometheus_client import Counter, Histogram
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
except ImportError:
    METRICS_AVAILABLE = False


logger = logging.getLogger(__name__)

class MexcHttp:
    """
    Лёгкая HTTP-обёртка для MEXC Spot V3 (публичные эндпоинты), без авторизации.

    ВАЖНО: реализованы таймауты и ретраи с backoff:
      - таймаут запроса: MEXC_REST_TIMEOUT (по умолчанию 10.0с)
      - попыток:        MEXC_REST_ATTEMPTS (по умолчанию 3)
      - базовый backoff: MEXC_REST_BACKOFF_BASE (по умолчанию 1.5с)
      - поддержка Retry-After при 429
      
    НОВОЕ в этой версии:
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
        ttl_sec: int = 60,  # Увеличен с 30 до 60
        ticker_24h_ttl_sec: int = 60  # NEW: TTL для 24h ticker
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._ttl_sec = ttl_sec
        self._ticker_24h_ttl_sec = ticker_24h_ttl_sec

        # Настройки таймаутов и ретраев из settings
        self._timeout_s: float = settings.rest_timeout_sec
        self._attempts: int = settings.rest_retry_attempts
        self._backoff_base_s: float = settings.rest_retry_backoff_ms / 1000.0  # ms → sec
        self._backoff_max_s: float = settings.rest_backoff_max_sec

        # Порог для логирования медленных запросов (можно добавить в settings позже)
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
        
        # NEW: Кэш для 24h ticker
        self._cache_ticker_24h: List[Dict[str, Any]] = []
        self._cache_ticker_24h_ts: float = 0.0
        
        # fee override per symbol: {"BTCUSDT": (maker, taker)}
        self._symbol_fee_override: Dict[str, Tuple[float, float]] = {}

        logger.info(
            f"MexcHttp initialized: base_url={base_url}, timeout={self._timeout_s}s, "
            f"attempts={self._attempts}, ttl={self._ttl_sec}s, ticker_24h_ttl={self._ticker_24h_ttl_sec}s"
        )

    # ---------- Вспомогательное ----------

    def _now(self) -> float:
        return time.time()

    def _sleep_backoff(self, attempt: int, retry_after_s: Optional[float] = None) -> None:
        """Экспоненциальная задержка с джиттером. Учитывает Retry-After, если передан."""
        if retry_after_s is not None and retry_after_s > 0:
            logger.debug(f"Using Retry-After={retry_after_s}s from MEXC")
            time.sleep(retry_after_s)
            return
        # exp backoff: base * 2^(attempt-1), c джиттером ±30%, ограничение max
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
    ) -> Any:  # Can return Dict or List
        """
        Универсальный запрос с ретраями:
        - ретраим таймауты/сетевые ошибки,
        - HTTP 429/5xx,
        - учитываем Retry-After при 429 (если есть).
        - добавлены метрики и логирование.
        """
        url = f"{self.base_url}{path}"
        endpoint = path.split("?")[0]  # Для метрик: /api/v3/depth
        
        attempts = max(1, self._attempts)
        last_exc: Optional[Exception] = None
        
        start_time = time.time()

        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    logger.warning(
                        f"MEXC retry attempt {attempt}/{attempts} for {endpoint}"
                    )
                
                resp = await  self._client.request(method, url, params=params, timeout=self._timeout_s)
                
                # Метрика latency
                duration = time.time() - start_time
                if METRICS_AVAILABLE:
                    mexc_rest_latency.labels(endpoint=endpoint).observe(duration)
                
                # Логирование медленных запросов
                if duration > self._slow_request_threshold_s:
                    logger.warning(
                        f"Slow MEXC request: {endpoint} took {duration:.2f}s (threshold={self._slow_request_threshold_s}s)"
                    )
                
                # 2xx — ок сразу
                if 200 <= resp.status_code < 300:
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(endpoint=endpoint, status='success').inc()
                    return resp.json()

                # 429/5xx — попробуем ретраить
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
                        f"MEXC rate limit (429) on {endpoint}, retry_after={retry_after_s}s, attempt {attempt}/{attempts}"
                    )

                if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                    if METRICS_AVAILABLE:
                        mexc_rest_requests.labels(endpoint=endpoint, status=f'http_{resp.status_code}').inc()
                    self._sleep_backoff(attempt, retry_after_s)
                    continue

                # Иначе — считаем ошибкой без ретрая
                if METRICS_AVAILABLE:
                    mexc_rest_requests.labels(endpoint=endpoint, status=f'http_{resp.status_code}').inc()
                
                logger.error(f"MEXC request failed with status {resp.status_code}: {endpoint}")
                resp.raise_for_status()
                return resp.json()

            except (httpx.TimeoutException, httpx.TransportError) as e:
                # Сетевые/таймаут — ретраим
                last_exc = e
                
                if METRICS_AVAILABLE:
                    mexc_rest_timeouts.labels(endpoint=endpoint).inc()
                
                logger.error(
                    f"MEXC timeout/transport error on {endpoint} (attempt {attempt}/{attempts}): {e}"
                )
                
                if attempt < attempts:
                    self._sleep_backoff(attempt)
                    continue
                
                if METRICS_AVAILABLE:
                    mexc_rest_requests.labels(endpoint=endpoint, status='timeout').inc()
                
                raise Exception(f"MEXC request failed after {attempts} retries (timeout/transport): {e}") from e

            except httpx.HTTPStatusError as e:
                # raise_for_status мог кинуться выше — уже обработали
                last_exc = e
                if METRICS_AVAILABLE:
                    mexc_rest_requests.labels(endpoint=endpoint, status='http_error').inc()
                logger.error(f"MEXC HTTP status error: {e}")
                break

            except Exception as e:
                last_exc = e
                if METRICS_AVAILABLE:
                    mexc_rest_requests.labels(endpoint=endpoint, status='error').inc()
                logger.error(f"MEXC unexpected error: {e}")
                break

        # Если вышли из цикла без return — бросаем последнюю исключение
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
        При отсутствии таких полей — просто игнорируем.
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

                # Поля верхнего уровня (маловероятно, но на всякий случай):
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

                # Внутри filters:
                for f in (s.get("filters") or []):
                    # Пример: {"filterType":"FEES","maker":"0.0000","taker":"0.0005"}
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
        Если override нет — дефолты (0% / 0.05%).
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
        """Возвращает список символов c метаданными и комиссиями maker/taker (если известны)."""
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

            out.append(
                {
                    "symbol": symbol,
                    "base": base,
                    "quote": quote,
                    "status": status,
                    "permissions": perms,
                    "maker_fee": mk,
                    "taker_fee": tk,
                    "zero_fee": zero,
                }
            )
        return out

    async def fetch_24h_tickers(self, quote: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        NEW: GET /api/v3/ticker/24hr (все символы с 24h метриками).
        Кэш: 60 секунд (данные обновляются медленно).
        
        Args:
            quote: Опциональная фильтрация по квоте (например, "USDT")
        
        Returns:
            List of dicts с полями: symbol, last, volume, quoteVolume, priceChangePercent,
            maker_fee, taker_fee, zero_fee
        """
        now = self._now()
        
        # Проверяем кэш
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
            
            # Нормализуем данные
            normalized: List[Dict[str, Any]] = []
            for t in raw_data:
                symbol = t.get("symbol")
                if not symbol:
                    continue
                
                mk, tk, zero = self._fees_for_symbol(symbol)
                
                normalized.append({
                    "symbol": symbol,
                    "last": float(t.get("lastPrice", 0.0)),
                    "volume": float(t.get("volume", 0.0)),  # base volume
                    "quoteVolume": float(t.get("quoteVolume", 0.0)),  # quote volume
                    "priceChangePercent": float(t.get("priceChangePercent", 0.0)),
                    "high": float(t.get("highPrice", 0.0)),
                    "low": float(t.get("lowPrice", 0.0)),
                    "maker_fee": mk,
                    "taker_fee": tk,
                    "zero_fee": zero,
                    "raw": t,
                })
            
            # Обновляем кэш
            self._cache_ticker_24h = normalized
            self._cache_ticker_24h_ts = now
            logger.info(f"Cached 24h tickers: {len(normalized)} symbols")
            data = normalized
        
        # Фильтрация по quote (если задано)
        if quote:
            quote_upper = quote.upper()
            data = [t for t in data if t["symbol"].endswith(quote_upper)]
            logger.debug(f"Filtered to {len(data)} symbols with quote={quote}")
        
        return data

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        GET /api/v3/ticker/bookTicker?symbol=BTCUSDT
        Возвращает bid/ask/last (last фоллбэк — среднее bid/ask).
        """
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
        """
        GET /api/v3/depth?symbol=BTCUSDT&limit=50
        Возвращает bids/asks: [[price, qty], ...]
        """
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
        """
        GET /api/v3/trades?symbol=BTCUSDT&limit=50
        Последние сделки. Нормализованный список: [{price, qty, isBuyerMaker, ts}, ...]
        """
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

            out.append(
                {
                    "price": price,
                    "qty": qty,
                    "isBuyerMaker": is_bm,
                    "ts": int(ts),
                    "raw": t,
                }
            )
        return out

    async def fetch_klines(
        self, 
        symbol: str, 
        interval: str = "1m", 
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        NEW: GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=100
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            interval: Timeframe (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles (default 100)
        
        Returns:
            List of candles: [{open_time, open, high, low, close, volume, ...}, ...]
        """
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        data = await self._request_with_retry("GET", self.KLINES, params=params)
        
        out: List[Dict[str, Any]] = []
        for k in data:
            # MEXC klines format: [open_time, open, high, low, close, volume, close_time, ...]
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
        """
        Если symbol задан — комиссии для конкретной пары.
        Иначе — дефолты для MEXC spot.
        """
        if symbol:
            mk, tk, zero = self._fees_for_symbol(symbol.upper())
            return {"symbol": symbol.upper(), "maker_fee": mk, "taker_fee": tk, "zero_fee": zero}
        return {
            "maker_fee": self.DEFAULT_MAKER_FEE,
            "taker_fee": self.DEFAULT_TAKER_FEE,
            "zero_fee": self.DEFAULT_MAKER_FEE == 0.0,
        }
    
    # ---------- Диагностика / метрики ----------
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        NEW: Возвращает статистику кэшей для мониторинга.
        """
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
        }
    
    def invalidate_cache(self, cache_type: Optional[str] = None) -> None:
        """
        NEW: Принудительно сбрасывает кэш.
        
        Args:
            cache_type: "exchange_info", "ticker_24h", или None (все кэши)
        """
        if cache_type is None or cache_type == "exchange_info":
            self._cache_exch = {}
            self._cache_exch_ts = 0.0
            logger.info("Invalidated exchange_info cache")
        
        if cache_type is None or cache_type == "ticker_24h":
            self._cache_ticker_24h = []
            self._cache_ticker_24h_ts = 0.0
            logger.info("Invalidated ticker_24h cache")