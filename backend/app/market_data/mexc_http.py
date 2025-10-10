# app/market_data/mexc_http.py  # Файл переименован в mexc_http.py для соответствия ошибке (было mex_http.py? — используй mexc_http.py)
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import time
import httpx  # Добавлено: для HTTP-запросов (установи pip install httpx если нет)


class MexcHttp:
    """
    Лёгкая HTTP-обёртка для MEXC Spot V3 (публичные эндпоинты), без авторизации.
    Базовые вызовы:
      - exchangeInfo (перечень инструментов, попытка получить per-symbol filters)
      - ticker/bookTicker (bid/ask/last)
      - depth (order book)
      - trades (последние сделки)

    Комиссии:
      - По умолчанию считаем maker_fee = 0.0, taker_fee = 0.0005 (0.05%) для spot.
      - Если в exchangeInfo появятся per-symbol fee-поля/filters — парсер заполнит override.

    Исправления:
      - Класс переименован в MexcHttp для точного соответствия ожидаемому импорту ('MexcHttp').
      - Самостоятельный httpx.Client (без внешнего HttpLike).
      - __init__ принимает base_url для гибкости.
      - Добавлена обработка ошибок в запросах.
      - Добавлен метод close() для cleanup.
      - Файл: используй имя 'mexc_http.py' (как в ошибке).
    """

    # API_VERSION = "/api/v3"  # Вынесено в пути
    EXCHANGE_INFO = "/api/v3/exchangeInfo"
    BOOK_TICKER = "/api/v3/ticker/bookTicker"
    DEPTH = "/api/v3/depth"
    TRADES = "/api/v3/trades"

    # Дефолтные комиссии для spot:
    DEFAULT_MAKER_FEE = 0.0       # 0%
    DEFAULT_TAKER_FEE = 0.0005    # 0.05%

    def __init__(self, base_url: str = "https://api.mexc.com", *, ttl_sec: int = 30) -> None:
        self.base_url = base_url.rstrip('/')
        self._ttl_sec = ttl_sec
        self._client = httpx.Client(timeout=10.0, follow_redirects=True)
        self._cache_exch: Dict[str, Any] = {}
        self._cache_exch_ts: float = 0.0
        # fee override per symbol: {"BTCUSDT": (maker, taker)}
        self._symbol_fee_override: Dict[str, Tuple[float, float]] = {}

    # ---------- ВСПОМОГАТЕЛЬНОЕ ----------

    def _now(self) -> float:
        return time.time()

    def _make_request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Внутренний метод для GET-запросов с обработкой ошибок."""
        url = f"{self.base_url}{path}"
        try:
            response = self._client.request(method, url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise Exception(f"MEXC HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise Exception(f"MEXC request failed: {e}")
        except Exception as e:
            raise Exception(f"Unexpected error in MEXC request: {e}")

    def _cached_exchange_info(self) -> Dict[str, Any]:
        if not self._cache_exch or (self._now() - self._cache_exch_ts) > self._ttl_sec:
            data = self._make_request('GET', self.EXCHANGE_INFO)
            self._parse_fees_from_exchange_info(data)
            self._cache_exch = data
            self._cache_exch_ts = self._now()
        return self._cache_exch

    def _parse_fees_from_exchange_info(self, data: Dict[str, Any]) -> None:
        """
        Если в exchangeInfo найдутся расширенные filters с комиссиями — сохраним их.
        При отсутствии таких полей — просто игнорируем.
        """
        try:
            symbols = data.get("symbols") or []
            for s in symbols:
                symbol = s.get("symbol")
                if not symbol:
                    continue
                maker_fee: Optional[float] = None
                taker_fee: Optional[float] = None

                # 1) Поля верхнего уровня (маловероятно, но на всякий случай):
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

                # 2) Внутри filters:
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
        except Exception:
            # Тихо игнорируем — формат мог не содержать комиссий
            pass

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

    def close(self) -> None:
        """Закрывает HTTP-клиент."""
        self._client.close()

    # ---------- ПУБЛИЧНЫЕ МЕТОДЫ ----------

    def fetch_symbols(self) -> List[Dict[str, Any]]:
        """
        Возвращает список символов c метаданными и комиссиями maker/taker (если известны).
        """
        data = self._cached_exchange_info()
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

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        GET /api/v3/ticker/bookTicker?symbol=BTCUSDT
        Возвращает bid/ask/last (last фоллбэк — среднее bid/ask).
        """
        params = {"symbol": symbol.upper()}
        data = self._make_request('GET', self.BOOK_TICKER, params=params)

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

    def fetch_orderbook(self, symbol: str, limit: int = 50) -> Dict[str, Any]:
        """
        GET /api/v3/depth?symbol=BTCUSDT&limit=50
        Возвращает bids/asks: [[price, qty], ...]
        """
        params = {"symbol": symbol.upper(), "limit": limit}
        data = self._make_request('GET', self.DEPTH, params=params)

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

    def fetch_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        GET /api/v3/trades?symbol=BTCUSDT&limit=50
        Последние сделки. Нормализованный список: [{price, qty, isBuyerMaker, ts}, ...]
        """
        params = {"symbol": symbol.upper(), "limit": limit}
        data = self._make_request('GET', self.TRADES, params=params)
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