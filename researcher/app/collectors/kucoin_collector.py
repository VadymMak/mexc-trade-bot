"""
KuCoin Futures mark-price collector.

KuCoin WS requires a token obtained via REST before connecting:
  POST https://api-futures.kucoin.com/api/v1/bullet-public  (no auth for public)
  → {"data": {"token": "...", "instanceServers": [{"endpoint": "wss://...", "pingInterval": 18000}]}}

Connect: {endpoint}?token={token}

Subscribe (comma-separated, max ~50 per message):
  {"id": "uid", "type": "subscribe", "topic": "/contractMarket/tickerV2:XBTUSDTM,ETHUSDTM", "response": true}

Inbound tick:
  {
    "type":    "message",
    "subject": "tickerV2",
    "topic":   "/contractMarket/tickerV2:XBTUSDTM",
    "data":    {"symbol": "XBTUSDTM", "price": "29123.45", "size": 1, "side": "buy", ...}
  }

Heartbeat: {"id": "uid", "type": "ping"} every ~18s → server replies {"type": "pong"}

Symbol mapping (USDT-margined perpetuals):
  BTC_USDT → XBTUSDTM   (BTC uses XBT legacy name)
  ETH_USDT → ETHUSDTM
  SOL_USDT → SOLUSDT M  → wait, it's SOLUSDT + M  (no underscore, no space)
  General:   {BASE}USDTM

Token refresh: tokens are valid ~18h; reconnect naturally refreshes token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress
from typing import Optional

import aiohttp
import websockets

from .base_collector import BaseCollector

logger = logging.getLogger(__name__)

_TOKEN_URL       = "https://api-futures.kucoin.com/api/v1/bullet-public"
_DEFAULT_WS_URL  = "wss://ws-api-futures.kucoin.com/"
_PING_INTERVAL   = 18.0   # seconds (KuCoin pingInterval = 18000ms)
_RECONNECT_DELAY = 5.0
_RECV_TIMEOUT    = 60.0   # KuCoin timeout = 30s, but give slack
_BATCH_SIZE      = 40     # symbols per subscribe message


# ── Symbol conversion ─────────────────────────────────────────────────────────

_BTC_OVERRIDE = "XBTUSDTM"   # KuCoin uses XBT for Bitcoin


def _to_kucoin_sym(symbol: str) -> str:
    """
    Convert internal symbol to KuCoin futures contract name.
      BTC_USDT → XBTUSDTM
      ETH_USDT → ETHUSDTM
      SOL_USDT → SOLUSDT M  ← just base+USDTM without underscore/space
    """
    base = symbol.upper().replace("_USDT", "").replace("_USD", "")
    if base == "BTC":
        return _BTC_OVERRIDE
    return f"{base}USDTM"


def _from_kucoin_sym(kucoin_sym: str, known: list[str]) -> str:
    """
    Reverse: XBTUSDTM → BTC_USDT, ETHUSDTM → ETH_USDT.
    Tries to match against known symbol list first.
    """
    # Build reverse map lazily from known list
    for s in known:
        if _to_kucoin_sym(s) == kucoin_sym:
            return s
    # Fallback: strip USDTM / handle XBTUSDTM
    if kucoin_sym == _BTC_OVERRIDE:
        return "BTC_USDT"
    if kucoin_sym.endswith("USDTM"):
        base = kucoin_sym[:-5]   # strip "USDTM"
        return f"{base}_USDT"
    return kucoin_sym


# ── Collector ─────────────────────────────────────────────────────────────────

class KucoinCollector(BaseCollector):
    """
    Streams KuCoin Futures last-trade prices for the given symbols.
    Reports prices under exchange name 'kucoin'.
    """

    def __init__(self) -> None:
        super().__init__("kucoin")
        self._symbols: list[str] = []
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def connect(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[KuCoin] Collector started for %d symbols", len(self._symbols))

    async def disconnect(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        logger.info("[KuCoin] Collector stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[KuCoin] %r — reconnecting in %.1fs", exc, _RECONNECT_DELAY)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=_RECONNECT_DELAY)

    async def _get_ws_url(self) -> str:
        """
        Fetch a short-lived WS token from KuCoin REST API.
        Returns the full WS URL with token query param.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_TOKEN_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
            token    = data["data"]["token"]
            endpoint = data["data"]["instanceServers"][0]["endpoint"]
            return f"{endpoint}?token={token}"
        except Exception as exc:
            logger.warning("[KuCoin] Failed to fetch WS token (%r) — using default URL", exc)
            return _DEFAULT_WS_URL

    async def _connect_and_stream(self) -> None:
        ws_url = await self._get_ws_url()
        logger.info("[KuCoin] Connecting → %s", ws_url[:60] + "…")

        async with websockets.connect(
            ws_url,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            max_size=4_194_304,
        ) as ws:
            # Wait for welcome message
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("type") != "welcome":
                    logger.debug("[KuCoin] First message was not welcome: %s", raw[:200])
            except Exception:
                pass

            # Subscribe in batches
            kucoin_syms = [_to_kucoin_sym(s) for s in self._symbols]
            subscribed = 0
            for i in range(0, len(kucoin_syms), _BATCH_SIZE):
                batch = kucoin_syms[i:i + _BATCH_SIZE]
                topic = "/contractMarket/tickerV2:" + ",".join(batch)
                sub = {
                    "id":       str(uuid.uuid4())[:8],
                    "type":     "subscribe",
                    "topic":    topic,
                    "response": True,
                }
                await ws.send(json.dumps(sub))
                subscribed += len(batch)
                await asyncio.sleep(0.1)   # avoid rate-limit

            logger.info("[KuCoin] Subscribed tickerV2: %d symbols", subscribed)

            hb_task = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop_evt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except asyncio.TimeoutError:
                        with suppress(Exception):
                            await ws.ping()
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    msg_type = msg.get("type", "")

                    # Skip control and ack messages
                    if msg_type in {"welcome", "ack", "pong", "ping"}:
                        continue
                    if msg_type != "message":
                        continue

                    subject = msg.get("subject", "")
                    if subject != "tickerV2":
                        continue

                    data = msg.get("data", {})
                    try:
                        price_raw = data.get("price")
                        if price_raw is None:
                            continue
                        price      = float(price_raw)
                        kucoin_sym = data.get("symbol", "")
                        if not kucoin_sym:
                            # fallback: extract from topic
                            topic = msg.get("topic", "")
                            kucoin_sym = topic.split(":")[-1] if ":" in topic else ""
                        symbol = _from_kucoin_sym(kucoin_sym, self._symbols)
                        ts_ms  = int(time.time() * 1000)
                        await self._notify(symbol, price, ts_ms)
                    except (ValueError, TypeError):
                        continue
            finally:
                hb_task.cancel()
                with suppress(Exception):
                    await hb_task

    async def _heartbeat(self, ws) -> None:
        """Send KuCoin ping every PING_INTERVAL seconds to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                with suppress(Exception):
                    await ws.send(json.dumps({
                        "id":   str(uuid.uuid4())[:8],
                        "type": "ping",
                    }))
        except asyncio.CancelledError:
            return
