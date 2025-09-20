# app/execution/live_executor.py
from __future__ import annotations

import asyncio
import hmac
import hashlib
import time
import uuid
from typing import Any, Dict, Optional, Literal

import httpx

from app.config.settings import settings

# ───────────────────────────── Настройки по умолчанию ─────────────────────────────
#
# Для MEXC Spot v3 приватные методы, как правило, используют:
#   - базу https://api.mexc.com/api/v3
#   - заголовок API-ключа вида X-MEXC-APIKEY (на некоторых совместимых API встречается X-MBX-APIKEY)
#   - подпись HMAC SHA256 по query-string, ключ = API_SECRET
#
# Мы оставим часть параметров конфигурируемыми через settings на случай расхождений в окружении.

DEFAULT_PRIVATE_BASE = (getattr(settings, "rest_base_url", "https://api.mexc.com/api/v3") or "").rstrip("/")
DEFAULT_APIKEY_HEADER_CANDIDATES = ("X-MEXC-APIKEY", "X-MBX-APIKEY")  # первый валидный будет использован
DEFAULT_RECV_WINDOW_MS = 5_000  # 5s стандартно
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=6.0, read=8.0)

Side = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]
TimeInForce = Literal["GTC", "IOC", "FOK"]


class LiveExecutor:
    """
    Каркас для приватных REST-вызовов с HMAC-подписью:
      - place_order / cancel_order / get_open_orders / get_account_info
    Безопасно по умолчанию: таймауты, ограничение параллелизма, простые ретраи.

    Привязка к твоему аккаунту/кошельку обеспечивается API Key/Secret
    (в settings: MEXC_API_KEY / MEXC_API_SECRET). Логин/пароль не используются.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PRIVATE_BASE,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_key_header: Optional[str] = None,
        recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
        proxy_url: Optional[str] = None,
        concurrency: int = 4,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or getattr(settings, "api_key", "")
        self.api_secret = (api_secret or getattr(settings, "api_secret", "")).encode("utf-8")
        self.api_key_header = api_key_header or self._detect_apikey_header()
        self.recv_window_ms = int(recv_window_ms)
        self._sem = asyncio.Semaphore(max(1, int(concurrency)))

        transport = httpx.AsyncHTTPTransport(proxy=proxy_url, retries=0) if proxy_url else None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={self.api_key_header: self.api_key} if self.api_key else {},
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            http2=False,
            trust_env=True,
        )

    # ───────────────────────────── Публичные методы ─────────────────────────────

    async def aclose(self) -> None:
        await self._client.aclose()

    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        time_in_force: TimeInForce = "GTC",
        client_order_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,  # на споте игнорируется; для совместимости интерфейса
    ) -> Dict[str, Any]:
        """
        Создать ордер. Для LIMIT требуется price + timeInForce.
        Возвращает ACK от биржи (dict).
        """
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": type,
            "quantity": self._fmt_num(quantity),
            "timestamp": self._ts(),
            "recvWindow": self.recv_window_ms,
        }
        if type == "LIMIT":
            if price is None:
                raise ValueError("price is required for LIMIT orders")
            params["price"] = self._fmt_num(price)
            params["timeInForce"] = time_in_force

        if client_order_id:
            params["newClientOrderId"] = client_order_id
        else:
            params["newClientOrderId"] = self._gen_client_order_id()

        # Некоторые спот-API принимают параметры в query (не JSON).
        return await self._signed_request("POST", "/order", params=params)

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: Optional[int] = None,
        orig_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Отменить ордер по orderId или origClientOrderId.
        """
        if not order_id and not orig_client_order_id:
            raise ValueError("either order_id or orig_client_order_id is required")

        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "timestamp": self._ts(),
            "recvWindow": self.recv_window_ms,
        }
        if order_id:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id

        return await self._signed_request("DELETE", "/order", params=params)

    async def get_open_orders(self, *, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Список открытых ордеров. Если symbol указан — фильтруем.
        """
        params: Dict[str, Any] = {
            "timestamp": self._ts(),
            "recvWindow": self.recv_window_ms,
        }
        if symbol:
            params["symbol"] = symbol.upper()
        return await self._signed_request("GET", "/openOrders", params=params)

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Балансы/лимиты аккаунта.
        """
        params: Dict[str, Any] = {
            "timestamp": self._ts(),
            "recvWindow": self.recv_window_ms,
        }
        return await self._signed_request("GET", "/account", params=params)

    # ───────────────────────────── Внутренние утилиты ─────────────────────────────

    def _detect_apikey_header(self) -> str:
        """
        Возвращает первый подходящий заголовок для API-ключа.
        По умолчанию пробуем X-MEXC-APIKEY, затем X-MBX-APIKEY.
        """
        # Можно сделать это настраиваемым через settings (api_key_header)
        hdr = getattr(settings, "api_key_header", "") or ""
        if hdr:
            return hdr
        return DEFAULT_APIKEY_HEADER_CANDIDATES[0]

    def _ts(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _fmt_num(x: float) -> str:
        # безопасное преобразование для query-string (без научной нотации)
        return format(float(x), "f")

    def _sign_query(self, query: str) -> str:
        return hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _signed_request(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: Dict[str, Any],
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Общий помощник: собирает query-string, добавляет подпись, делает запрос.
        Лёгкие ретраи на сетевых ошибках и 429/5xx.
        """
        # Собираем query-string вручную, сохраняя порядок (важно для подписи)
        # httpx сам сделает URL-кодирование из dict, но подпись должна считаться
        # по исходной строке. Поэтому используем .params= и .build_request(), чтобы
        # вытащить финальную строку, затем подсунуть signature.
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt < max_retries:
            attempt += 1
            try:
                # 1) строим временно без signature
                req = self._client.build_request(method, path, params=params)
                # 2) выдёргиваем собранную строку запроса
                query_without_sig = req.url.query.decode("utf-8")
                signature = self._sign_query(query_without_sig)
                # 3) финальный запрос — добавляем signature как последний параметр
                signed_params = dict(params)
                signed_params["signature"] = signature

                async with self._sem:
                    resp = await self._client.request(method, path, params=signed_params)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, (dict, list)):
                    return {"data": data}
                return data if isinstance(data, dict) else {"data": data}
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # Простая политика ретраев
                if status in (408, 429, 500, 502, 503, 504) and attempt < max_retries:
                    await asyncio.sleep(0.4 * attempt)
                    continue
                # Попробуем вернуть полезный payload ошибки, если он есть
                try:
                    return {"error": True, "status": status, "payload": e.response.json()}
                except Exception:
                    return {"error": True, "status": status, "payload": e.response.text[:512]}
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise

        # Если все попытки исчерпаны и исключения «съедены»
        if last_exc:
            raise last_exc
        return {"error": True, "status": -1, "payload": "unknown error"}

    @staticmethod
    def _gen_client_order_id(prefix: str = "bot") -> str:
        """
        Идемпотентный clientOrderId. 32 символа укладывается почти везде.
        """
        return f"{prefix}_{uuid.uuid4().hex[:24]}"
