# app/market_data/ws_client.py
from __future__ import annotations

import asyncio
import json
import random
import socket
import ssl
import sys
import time
import logging
from typing import Any, Iterable, List, Optional, Tuple

import math
from contextlib import suppress

import websockets

from app.config.settings import settings
from app.market_data.helpers.proto_utils import (
    maybe_gunzip,
    hexdump,
    extract_frames,
    debug_envelope_shape,
    collect_bytes_candidates,
    find_book_ticker_cls,
    find_depth_cls,
    bruteforce_decode_book,
)
from app.market_data.helpers.quote_logging import QuoteLogger

# ✅ Gate client export (kept for compatibility)
from app.market_data.gate_ws import GateWebSocketClient

logger = logging.getLogger(__name__)

# ── metrics & health (minimal integration) ──────────────────────────────────
try:
    from app.infra.metrics import (
        ticks_total,
        ws_lag_seconds,
        ws_reconnects_total,
        ws_active_subscriptions,
    )
    METRICS_AVAILABLE = True
except Exception:
    ticks_total = None
    ws_lag_seconds = None
    ws_reconnects_total = None
    ws_active_subscriptions = None
    METRICS_AVAILABLE = False

try:
    from app.infra.health import ws_health
    HEALTH_AVAILABLE = True
except Exception:
    ws_health = None
    HEALTH_AVAILABLE = False

# ── constants (env-driven via settings/constants) ───────────────────────────
try:
    from app.config.constants import (
        WS_MAX_TOPICS,
        WS_PING_INTERVAL_SEC,
        WS_MAX_LIFETIME_SEC,
        WS_PUBLIC_ENDPOINT,
        WS_CHANNELS,
        WS_RATE_SUFFIX,
        WS_SUBSCRIBE_RATE_LIMIT_PER_SEC,
    )
except Exception:
    WS_MAX_TOPICS = 30
    WS_PING_INTERVAL_SEC = 20
    WS_MAX_LIFETIME_SEC = 23 * 3600
    WS_PUBLIC_ENDPOINT = "wss://wbs-api.mexc.com/ws"
    WS_CHANNELS = {
        "BOOK_TICKER": "spot@public.aggre.bookTicker.v3.api.pb",
        "DEALS": "spot@public.aggre.deals.v3.api.pb",
        "DEPTH_LIMIT": "spot@public.limit.depth.v3.api.pb",
    }
    WS_RATE_SUFFIX = "@500ms"
    WS_SUBSCRIBE_RATE_LIMIT_PER_SEC = 8

# ── service callbacks ───────────────────────────────────────────────────────
try:
    from app.services.book_tracker import (
        on_book_ticker as _bt_cb,
        on_partial_depth as _depth_cb,
        update_tape_metrics,
    )
    BOOK_TRACKER_AVAILABLE = True
except Exception:
    BOOK_TRACKER_AVAILABLE = False
    logger.warning("book_tracker module not fully available, using fallback stubs")
    
    from app.services.book_tracker import book_tracker as _book_tracker

    async def _bt_cb(symbol: str, bid: float, bid_qty: float, ask: float, ask_qty: float, ts_ms: Optional[int]):
        logger.debug(f"📊 Book ticker stub for {symbol}: bid={bid:.4f}, ask={ask:.4f}, ts_ms={ts_ms}")

    async def _depth_cb(symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts_ms: Optional[int]):
        return

    async def update_tape_metrics(
        symbol: str, usdpm: float, tpm: float, trades: Optional[List[Tuple[float, float, int]]] = None
    ):
        if usdpm > 0 or tpm > 0:
            logger.debug(f"Updated tape for {symbol}: usdpm={usdpm:.2f}, tpm={tpm:.1f}")
        if trades:
            if not hasattr(_book_tracker, "recent_trades"):
                _book_tracker.recent_trades = {}
            if not hasattr(_book_tracker, "usdpm"):
                _book_tracker.usdpm = {}
            if not hasattr(_book_tracker, "tpm"):
                _book_tracker.tpm = {}
            if not hasattr(_book_tracker, "atr_proxy"):
                _book_tracker.atr_proxy = {}
            if not hasattr(_book_tracker, "vol_pattern"):
                _book_tracker.vol_pattern = {}

            old_trades = _book_tracker.recent_trades.get(symbol, [])
            all_trades = old_trades + trades
            _book_tracker.recent_trades[symbol] = all_trades[-100:]

            _book_tracker.usdpm[symbol] = usdpm
            _book_tracker.tpm[symbol] = tpm

            def _compute_volatility_proxy(data: List[Tuple[float, float, int]]) -> float:
                prices = []
                for x in data[-20:]:
                    with suppress(Exception):
                        p = float(x[0])
                        if p > 0:
                            prices.append(p)
                if len(prices) < 2:
                    return 0.0
                deltas = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
                if deltas:
                    return sum(deltas) / len(deltas)
                mean_p = sum(prices) / len(prices)
                return math.sqrt(sum((p - mean_p) ** 2 for p in prices) / len(prices))

            atr = _compute_volatility_proxy(_book_tracker.recent_trades[symbol])
            _book_tracker.atr_proxy[symbol] = atr
            logger.debug(f"Computed atr_proxy={atr:.2f} for {symbol}, total trades now={len(_book_tracker.recent_trades[symbol])}")

            vols = [float(x[1]) for x in _book_tracker.recent_trades[symbol][-20:] if float(x[1]) > 0]
            if len(vols) < 5:
                prices = [float(x[0]) for x in _book_tracker.recent_trades[symbol][-20:] if float(x[0]) > 0]
                if len(prices) >= 5:
                    returns = [abs((prices[i] - prices[i - 1]) / prices[i - 1]) for i in range(1, len(prices))]
                    vols = returns
                else:
                    vp = 0
                    ratio = float("inf")
            else:
                mean_v = sum(vols) / len(vols)
                std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vols) / len(vols))
                ratio = std_v / mean_v if mean_v > 0 else float("inf")
                score = max(0, min(100, 100 - (ratio * 100)))
                if ratio < 0.3:
                    score += 10
                vp = min(100, score)
            _book_tracker.vol_pattern[symbol] = int(vp)
            logger.debug(f"Computed vol_pattern={vp} for {symbol} (ratio={ratio:.3f})")

# ── protobuf modules ────────────────────────────────────────────────────────
PROTO_AVAILABLE = False
EnvelopeModule = None
BookTickerModule = None
AggreBookTickerModule = None
DepthModule = None
DealsModule = None
try:
    from app.market_data.mexc_pb import (
        PushDataV3ApiWrapper_pb2 as EnvelopeModule,
        PublicBookTickerV3Api_pb2 as BookTickerModule,
        PublicAggreBookTickerV3Api_pb2 as AggreBookTickerModule,
        PublicLimitDepthsV3Api_pb2 as DepthModule,
        PublicAggreDealsV3Api_pb2 as DealsModule,
    )
    PROTO_AVAILABLE = True
    logger.info("✅ Protobuf modules loaded successfully")
except Exception as e:
    logger.error(f"⚠️ Protobuf decoders not available: {e}")
    PROTO_AVAILABLE = False
    EnvelopeModule = None


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── small metric helpers ────────────────────────────────────────────────────
def _metric_inc(counter, **labels) -> None:
    if not METRICS_AVAILABLE or counter is None:
        return
    try:
        if hasattr(counter, "labels") and labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception as e:
        logger.debug(f"Metric increment failed: {e}")


def _metric_set(gauge, value: float) -> None:
    if not METRICS_AVAILABLE or gauge is None:
        return
    try:
        gauge.set(value)
    except Exception as e:
        logger.debug(f"Metric set failed: {e}")


def _metric_observe(hist, value: float, **labels) -> None:
    if not METRICS_AVAILABLE or hist is None:
        return
    try:
        if hasattr(hist, "labels") and labels:
            hist.labels(**labels).observe(value)
        else:
            hist.observe(value)
    except Exception as e:
        logger.debug(f"Metric observe failed: {e}")


def _health_started() -> None:
    if not HEALTH_AVAILABLE or ws_health is None:
        return
    try:
        ws_health.mark_started()
    except Exception as e:
        logger.debug(f"Health mark_started failed: {e}")


def _health_tick() -> None:
    if not HEALTH_AVAILABLE or ws_health is None:
        return
    try:
        ws_health.mark_tick()
    except Exception as e:
        logger.debug(f"Health mark_tick failed: {e}")


def _health_stopped() -> None:
    if not HEALTH_AVAILABLE or ws_health is None:
        return
    try:
        ws_health.mark_stopped()
    except Exception as e:
        logger.debug(f"Health mark_stopped failed: {e}")


def _resolve_channels(channels: Optional[List[str]]) -> List[str]:
    """
    Accept either channel KEYS (e.g. 'BOOK_TICKER') or full topics.
    Also map some older names.
    """
    old_to_new = {
        "spot@public.bookTicker.v3.api": "spot@public.aggre.bookTicker.v3.api.pb",
        "spot@public.aggre.deals.v3.api": "spot@public.aggre.deals.v3.api.pb",
        "spot@public.limit.depth.v3.api": "spot@public.limit.depth.v3.api.pb",
    }

    if not channels or not any(channels):
        return [
            WS_CHANNELS.get("BOOK_TICKER", "spot@public.aggre.bookTicker.v3.api.pb"),
            WS_CHANNELS.get("DEALS", "spot@public.aggre.deals.v3.api.pb"),
            WS_CHANNELS.get("DEPTH_LIMIT", "spot@public.limit.depth.v3.api.pb"),
        ]
    out: List[str] = []
    for ch in channels:
        if not ch:
            continue
        ch = ch.strip()
        if ch in old_to_new:
            orig = ch
            mapped = old_to_new[orig]
            ch = mapped
            logger.info(f"🔄 Fallback: mapped old '{orig}' to new '{mapped}'")
        if ch.startswith("spot@"):
            out.append(ch)
        else:
            mapped = WS_CHANNELS.get(ch)
            out.append(mapped if mapped else ch)
    return out


# ── helpers ─────────────────────────────────────────────────────────────────
_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")


def _looks_like_quote_only(sym: str) -> bool:
    s = sym.upper().strip()
    if s in _QUOTES:
        return True
    if len(s) < 6:
        return True
    return False


class MEXCWebSocketClient:
    """
    Public MEXC Spot WS client (v3) with improved error handling and logging.
    
    Features:
      • Subscribes to book-ticker, deals, and depth topics (protobuf frames).
      • Normalizes events and forwards to book_tracker.
      • Robust against 'Blocked!' acks; auto-downgrades and re-subscribes.
      • Comprehensive logging and metrics.
      • Connection lifecycle management (max lifetime, reconnection with backoff).
    
    Improvements in this version:
      • Better structured logging (logger instead of print statements).
      • Enhanced error handling with context.
      • Metrics availability checks.
      • Connection state tracking.
      • Graceful degradation when dependencies unavailable.
    """
    MAX_TOPICS_PER_CONN = WS_MAX_TOPICS

    def __init__(
        self,
        symbols: List[str],
        channels: Optional[List[str]] = None,
        rate_suffix: str = WS_RATE_SUFFIX,
        reconnect_floor: float = 0.5,
        reconnect_ceil: float = 30.0,
        subscribe_rate_per_sec: int = WS_SUBSCRIBE_RATE_LIMIT_PER_SEC,
    ):
        # Filter and normalize symbols
        raw_syms = [s for s in symbols if s and str(s).strip()]
        self.symbols = []
        for s in raw_syms:
            s_up = s.strip().upper()
            if _looks_like_quote_only(s_up):
                logger.warning(
                    f"Skip non-tradable/quote-only symbol in WS: '{s_up}' "
                    f"(need BASEQUOTE format, e.g. BTCUSDT)"
                )
                continue
            self.symbols.append(s_up)

        if not self.symbols:
            logger.warning("No valid symbols provided to MEXCWebSocketClient")

        self.channels = _resolve_channels(channels)
        self.rate_suffix = rate_suffix
        self.ws_url = (
            getattr(settings, "ws_base_url_resolved", None)
            or getattr(settings, "ws_url_public", None)
            or WS_PUBLIC_ENDPOINT
        )

        logger.info(
            f"Initializing MEXC WS client: symbols={len(self.symbols)}, "
            f"channels={self.channels}, rate_suffix={rate_suffix}, url={self.ws_url}"
        )

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._want_stop = False
        self._id_counter = 1
        self._subscribed_topics: set[str] = set()

        self._reconnect_floor = reconnect_floor
        self._reconnect_ceil = reconnect_ceil
        self._reconnect_delay = reconnect_floor

        self._last_recv_ts_ms: int = 0
        self._last_ping_ts_ms: int = 0
        self._started_at_ms: int = 0

        # subscription rate-limit
        self._subs_per_sec = max(1, int(subscribe_rate_per_sec))
        self._sub_interval = 1.0 / float(self._subs_per_sec)
        
        logger.info(
            f"Subscription rate limit: {self._subs_per_sec} topics/sec "
            f"(interval={self._sub_interval:.3f}s)"
        )

        # auto-downgrade state
        self._blocked_seen = 0  # 0 → none, 1 → drop rate suffix, 2+ → also drop "aggre"
        self._downgraded_once = False

        # codecs
        self._book_ticker_cls: Optional[type] = None
        self._depth_cls: Optional[type] = None
        self._deals_cls: Optional[type] = None
        self._book_modules = [m for m in (AggreBookTickerModule, BookTickerModule) if m]

        # logging helpers
        self._verbose_frames = bool(getattr(settings, "ws_verbose_frames", False))
        self._verbose_hexdump = bool(getattr(settings, "ws_verbose_hexdump", False))
        self._enable_bruteforce = bool(getattr(settings, "ws_enable_bruteforce", False))
        self._dbg_json_parity = bool(getattr(settings, "ws_debug_json_parity", False))
        self._dbg_pb_variants = bool(getattr(settings, "ws_debug_pb_variants", False))

        self._quote_logger = QuoteLogger(
            log_throttle_ms=int(getattr(settings, "ws_log_throttle_ms", 2000)),
            summary_every_ms=int(getattr(settings, "ws_summary_every_ms", 5000)),
        )
        
        # Statistics
        self._total_reconnects = 0
        self._total_messages_received = 0
        self._total_book_tickers = 0
        self._total_deals = 0
        self._total_depth_updates = 0

    # ───────────── lifecycle ─────────────
    async def run(self) -> None:
        """Main run loop with connection lifecycle management."""
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            logger.error("❌ Protobuf decoders not available — cannot run WS client.")
            return
        if not self.symbols:
            logger.warning("⚠️ No symbols to subscribe — WS client not started.")
            return

        logger.info(f"🚀 Starting MEXC WS client for {len(self.symbols)} symbols")
        _health_started()

        self._want_stop = False
        try:
            while not self._want_stop:
                try:
                    await self._connect()
                    await self._subscribe_all()
                    await self._listen_loop()
                except asyncio.CancelledError:
                    logger.info("WS client cancelled")
                    raise
                except Exception as e:
                    if self._want_stop:
                        break
                    logger.error(f"❌ WS loop error: {e}", exc_info=True)
                    self._total_reconnects += 1
                await self._reconnect_sleep()
        except asyncio.CancelledError:
            logger.info("WS client task cancelled, shutting down")
        finally:
            await self._graceful_close()
            _health_stopped()
            logger.info(
                f"📊 WS client stopped. Stats: reconnects={self._total_reconnects}, "
                f"messages={self._total_messages_received}, book_tickers={self._total_book_tickers}, "
                f"deals={self._total_deals}, depth={self._total_depth_updates}"
            )

    async def stop(self) -> None:
        """Gracefully stop the WebSocket client."""
        logger.info("⏹️ Stopping MEXC WS client...")
        self._want_stop = True
        
        # Attempt to unsubscribe
        try:
            if self._ws and self._connected and self._subscribed_topics:
                logger.info(f"Unsubscribing from {len(self._subscribed_topics)} topics...")
                for t in list(self._subscribed_topics):
                    try:
                        await self._send_json(
                            {"method": "UNSUBSCRIPTION", "params": [t], "id": self._next_id()}
                        )
                        await asyncio.sleep(0.01)  # Small delay between unsubscribes
                    except Exception as e:
                        logger.debug(f"Error unsubscribing from {t}: {e}")
        except Exception as e:
            logger.warning(f"Error during unsubscribe: {e}")
        
        await self._graceful_close()

    async def _graceful_close(self) -> None:
        """Close WebSocket connection gracefully."""
        if not self._ws:
            return
            
        try:
            logger.debug("Closing WebSocket connection...")
            try:
                await asyncio.wait_for(self._ws.close(), timeout=2.0)
                logger.debug("WebSocket closed gracefully")
            except asyncio.TimeoutError:
                logger.warning("WebSocket close timeout, forcing close")
                try:
                    self._ws.transport.close()  # type: ignore[attr-defined]
                except Exception as e:
                    logger.debug(f"Error forcing transport close: {e}")
        except Exception as e:
            logger.error(f"Error during graceful close: {e}")
        finally:
            self._ws = None
            self._connected = False
            self._subscribed_topics.clear()

    # ───────────── connect/subscribe/listen ─────────────
    async def _connect(self) -> None:
        """Establish WebSocket connection with comprehensive logging."""
        logger.info(f"🔌 Connecting to {self.ws_url}...")

        # DNS diagnostics
        try:
            host = self.ws_url.split("://", 1)[-1].split("/", 1)[0]
            path = self.ws_url.split(host, 1)[-1]
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addrs = [f"{ai[4][0]}" for ai in infos]
            logger.debug(f"🔎 DNS {host} → {addrs or '[]'}")
        except Exception as e:
            logger.warning(f"DNS resolve error for {self.ws_url}: {e}")
            host = self.ws_url.split("://", 1)[-1].split("/", 1)[0]
            path = self.ws_url.split(host, 1)[-1]

        ws_uri = self.ws_url
        server_hostname = getattr(settings, "ws_server_hostname", host)
        ws_dns_override = (getattr(settings, "ws_dns_override", "") or "").strip()

        ssl_ctx = None
        server_hostname_kw: dict[str, Any] = {}
        
        if ws_dns_override:
            ip = ws_dns_override
            ws_uri = f"wss://{ip}{path}"
            ssl_ctx = ssl.create_default_context()
            server_hostname_kw = {"server_hostname": server_hostname}
            logger.info(f"🛠️ WS_DNS_OVERRIDE active: connecting to {ip} with SNI={server_hostname}")

        connect_kwargs = dict(
            ping_interval=None,  # Using JSON PINGs instead
            ping_timeout=None,
            max_size=None,
            open_timeout=getattr(settings, "ws_open_timeout", 20),
            close_timeout=getattr(settings, "ws_close_timeout", 5),
        )
        if ssl_ctx is not None:
            connect_kwargs["ssl"] = ssl_ctx
        connect_kwargs.update(server_hostname_kw)

        try:
            self._ws = await websockets.connect(ws_uri, **connect_kwargs)
        except Exception as e:
            logger.error(f"❌ WebSocket connection failed: {e}")
            raise

        self._connected = True
        now = _now_ms()
        self._last_recv_ts_ms = now
        self._last_ping_ts_ms = 0
        self._reconnect_delay = self._reconnect_floor
        self._subscribed_topics.clear()
        self._started_at_ms = now
        self._blocked_seen = 0
        self._downgraded_once = False
        
        logger.info("✅ WebSocket connected successfully")

        _metric_inc(ws_reconnects_total)

        # Resolve protobuf classes once per connection
        if self._book_ticker_cls is None:
            for mod in [m for m in (AggreBookTickerModule, BookTickerModule) if m]:
                self._book_ticker_cls = find_book_ticker_cls(mod)
                if self._book_ticker_cls:
                    logger.debug(f"Found book ticker class: {self._book_ticker_cls.__name__}")
                    break
            if self._book_ticker_cls is None:
                logger.warning("No suitable book-ticker class found in protobuf modules")
                
        if DepthModule and self._depth_cls is None:
            self._depth_cls = find_depth_cls(DepthModule)
            if self._depth_cls:
                logger.debug(f"Found depth class: {self._depth_cls.__name__}")
                
        if DealsModule and self._deals_cls is None:
            self._deals_cls = getattr(DealsModule, "PublicAggreDealsV3Api", None)
            if self._deals_cls is None:
                for typ in DealsModule.DESCRIPTOR.message_types_by_name.values():
                    try_cls = getattr(DealsModule, typ.name, None)
                    if try_cls and ("deals" in typ.name.lower() or "public" in typ.name.lower()):
                        self._deals_cls = try_cls
                        break
            if self._deals_cls:
                logger.debug(f"Found deals class: {self._deals_cls.__name__}")
            else:
                logger.warning("No suitable deals class found in PublicAggreDealsV3Api_pb2")

    def _topic_for(self, ch: str, sym: str, levels: int) -> str:
        """Build topic string with current downgrade state and rate suffix."""
        topic = ch
        if self._blocked_seen >= 2:
            topic = topic.replace(".aggre.bookTicker.", ".bookTicker.")
        ch_l = topic.lower()
        if (".bookticker." in ch_l) or (".aggre.depth." in ch_l) or (".deals." in ch_l):
            suf = "" if self._blocked_seen >= 1 else self.rate_suffix
            return f"{topic}{suf}@{sym}"
        elif ".limit.depth." in ch_l:
            return f"{topic}@{sym}@{levels}"
        else:
            suf = "" if self._blocked_seen >= 1 else self.rate_suffix
            return f"{topic}{suf}@{sym}"

    async def _subscribe_all(self) -> None:
        """Subscribe to all topics with rate limiting."""
        assert self._ws and self._connected, "Must be connected before subscribing"
        
        topics: list[str] = []
        levels = int(getattr(settings, "ws_orderbook_snapshot_levels", 10))

        for sym in self.symbols:
            for ch in self.channels:
                topics.append(self._topic_for(ch, sym, levels))

            # Optional debug topics
            if self._dbg_json_parity:
                suf = "" if self._blocked_seen >= 1 else self.rate_suffix
                topics.append(f"spot@public.aggre.bookTicker.v3.api{suf}@{sym}")
            if self._dbg_pb_variants:
                suf = "" if self._blocked_seen >= 1 else self.rate_suffix
                topics.append(f"spot@public.bookTicker.v3.api.pb{suf}@{sym}")

        if len(topics) > self.MAX_TOPICS_PER_CONN:
            logger.error(
                f"❌ Too many topics ({len(topics)}) for single WS connection. "
                f"Max={self.MAX_TOPICS_PER_CONN}. Consider sharding."
            )
            raise RuntimeError(
                f"Too many topics ({len(topics)}) for a single WS. Shard needed."
            )

        logger.info(f"📡 Subscribing to {len(topics)} topics (rate: {self._subs_per_sec}/sec)...")
        _metric_set(ws_active_subscriptions, float(len(topics)))

        # Rate-limited subscription sends
        for i, t in enumerate(topics, 1):
            try:
                await self._send_json(
                    {"method": "SUBSCRIPTION", "params": [t], "id": self._next_id()}
                )
                if i % 10 == 0 or i == len(topics):
                    logger.debug(f"📡 Subscribed {i}/{len(topics)} topics")
                await asyncio.sleep(self._sub_interval)
            except Exception as e:
                logger.error(f"Failed to subscribe to {t}: {e}")

        logger.info(f"✅ Subscription phase complete ({len(topics)} topics)")

    async def _listen_loop(self) -> None:
        """Main message reception loop with heartbeat and lifecycle management."""
        assert self._ws and self._connected
        ws = self._ws
        
        logger.debug("👂 Starting listen loop...")

        while not self._want_stop:
            now = _now_ms()

            # JSON PING if idle
            idle_ms = now - self._last_recv_ts_ms
            if (
                idle_ms > WS_PING_INTERVAL_SEC * 1000
                and now - self._last_ping_ts_ms > WS_PING_INTERVAL_SEC * 1000
            ):
                try:
                    await self._send_json({"method": "PING", "id": self._next_id()})
                    self._last_ping_ts_ms = now
                    logger.debug(f"↪️ JSON PING sent (idle for {idle_ms/1000:.1f}s)")
                except Exception as e:
                    logger.warning(f"Failed to send PING: {e}")

            # Cycle connection periodically
            lifetime_sec = (now - self._started_at_ms) / 1000
            if lifetime_sec > WS_MAX_LIFETIME_SEC:
                logger.info(
                    f"♻️ Max lifetime reached ({lifetime_sec:.0f}s > {WS_MAX_LIFETIME_SEC}s) — cycling connection"
                )
                break

            # Receive message with timeout
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                if self._want_stop:
                    break
                continue
            except asyncio.CancelledError:
                logger.debug("Listen loop cancelled")
                break
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"❌ WebSocket closed: {e}")
                break
            except Exception as e:
                if self._want_stop:
                    break
                logger.error(f"🟡 recv error: {e}")
                break

            self._last_recv_ts_ms = _now_ms()
            self._total_messages_received += 1

            # Route message by type
            if isinstance(message, (bytes, bytearray)):
                if self._verbose_frames:
                    logger.debug(f"🔹 WS frame: type=bytes len={len(message)} head={hexdump(message[:32])}")
                await self._handle_binary(message)
            else:
                await self._handle_text(message)

        logger.debug("👂 Listen loop exited")
        self._connected = False

    # ───────────── handlers ─────────────
    async def _handle_text(self, message: str) -> None:
        """Handle text (JSON) messages: ACKs, heartbeats, errors."""
        try:
            data = json.loads(message)
        except Exception as e:
            if self._verbose_frames:
                logger.warning(f"🟡 Text message (non-JSON?): {message[:200]}")
            return

        # ACKs
        if "code" in data and "msg" in data:
            code = data.get("code")
            msg = str(data.get("msg", ""))
            
            if code == 0:
                # MEXC uses code=0 for both success and some "Blocked!" notices
                if "Not Subscribed successfully" in msg and "Blocked" in msg:
                    self._blocked_seen += 1
                    logger.warning(f"🧱 ACK Blocked! count={self._blocked_seen} msg={msg}")
                    
                    if not self._downgraded_once or self._blocked_seen >= 2:
                        await self._downgrade_and_resubscribe()
                else:
                    # Successful subscription
                    if msg.startswith("spot@"):
                        self._subscribed_topics.add(msg)
                        logger.debug(f"✅ Subscribed: {msg}")
                    # Reset block counter on healthy ack
                    if self._blocked_seen > 0:
                        self._blocked_seen = 0
                    logger.debug(f"✅ ACK code=0 msg={msg[:100]}")
            else:
                logger.error(f"❗ ACK error code={code} msg={msg}")
            return

        # Heartbeats
        if data.get("ping") is not None or data.get("pong") is not None:
            if self._verbose_frames:
                logger.debug(f"🫧 Heartbeat ack: {data}")
            return

        # Other service messages
        if self._verbose_frames:
            logger.debug(f"ℹ️ Service message: {data}")

    async def _downgrade_and_resubscribe(self) -> None:
        """
        Handle 'Blocked!' by downgrading subscription policy:
        - First downgrade: drop rate suffix
        - Second downgrade: also drop 'aggre' variant
        Then re-subscribe with new policy.
        """
        self._downgraded_once = True
        
        logger.warning(
            f"🔽 Downgrading subscription policy: "
            f"drop_rate_suffix={self._blocked_seen>=1}, drop_aggre={self._blocked_seen>=2}"
        )
        
        try:
            await self._unsubscribe_all_silent()
        except Exception as e:
            logger.warning(f"Error during unsubscribe: {e}")
        
        self._subscribed_topics.clear()
        await asyncio.sleep(0.5)  # Brief pause before re-subscribing
        await self._subscribe_all()

    async def _unsubscribe_all_silent(self) -> None:
        """Unsubscribe from all topics without raising errors."""
        if not self._ws or not self._connected:
            return
        
        logger.debug(f"Unsubscribing from {len(self._subscribed_topics)} topics...")
        for t in list(self._subscribed_topics):
            try:
                await self._send_json(
                    {"method": "UNSUBSCRIPTION", "params": [t], "id": self._next_id()}
                )
            except Exception as e:
                logger.debug(f"Error unsubscribing from {t}: {e}")
            await asyncio.sleep(0.01)

    async def _handle_binary(self, payload: bytes) -> None:
        """Handle binary (protobuf) messages: book ticker, deals, depth."""
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            logger.warning("🟡 Binary frame but protobuf env not available")
            return

        # Decompress if gzipped
        payload, was_gz = maybe_gunzip(payload)
        if self._verbose_hexdump and was_gz:
            logger.debug(f"🗜️ gunzipped payload len={len(payload)} head={hexdump(payload[:32])}")

        try:
            desc = getattr(EnvelopeModule, "DESCRIPTOR", None)
            if not desc:
                logger.warning("🟡 Envelope module without DESCRIPTOR")
                return
        except Exception as e:
            logger.error(f"❌ Protobuf parse error: {e}")
            return

        parsed_any = False
        dbg = logger.debug if self._verbose_frames else None

        # Try to parse with all available envelope types
        for typ in EnvelopeModule.DESCRIPTOR.message_types_by_name.values():
            try_cls = getattr(EnvelopeModule, typ.name, None)
            if try_cls is None:
                continue

            msg = try_cls()
            try:
                msg.ParseFromString(payload)
            except Exception:
                continue

            # Extract frames from parsed envelope
            frames = list(extract_frames(msg, debug_cb=dbg))
            if not frames:
                if self._verbose_frames:
                    debug_envelope_shape(msg)
                    cands = collect_bytes_candidates(msg)
                    if cands:
                        cands_sorted = sorted(cands, key=lambda kv: len(kv[1]), reverse=True)
                        top = ", ".join([f"{p}={len(b)}" for p, b in cands_sorted[:6]])
                        logger.debug(f"🧪 Wrapper bytes candidates (path=len): {top}")
                continue

            parsed_any = True
            
            # Process each frame
            for ch, sym, ts, data_bytes in frames:
                ch_str = str(ch)
                
                if self._verbose_frames:
                    logger.debug(
                        f"🧵 Frame extracted: ch={ch_str} sym={sym or ''} "
                        f"ts={int(ts or 0)} bytes={len(data_bytes)}"
                    )
                
                # Route to appropriate handler
                if "bookTicker" in ch_str:
                    self._on_book_ticker(sym or "", data_bytes, int(ts or 0))
                elif ".deals." in ch_str:
                    self._on_deals(sym or "", data_bytes, int(ts or 0))
                elif ".limit.depth" in ch_str or ".increase.depth" in ch_str or "Depth" in ch_str:
                    self._on_depth(sym or "", data_bytes, int(ts or 0))
                else:
                    if self._verbose_frames:
                        logger.debug(f"ℹ️ Unhandled channel: {ch_str} ({len(data_bytes)} bytes)")
            break

        if not parsed_any and self._verbose_frames:
            logger.warning("🟡 Protobuf envelope parsed but no frames found")

    # ───────────── domain parsers ─────────────
    def _on_book_ticker(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        """Parse and process book ticker message."""
        if self._want_stop:
            return

        data_bytes, _ = maybe_gunzip(data_bytes)
        primary_cls = self._book_ticker_cls

        def try_extract(m) -> Optional[tuple[float, float, float, float]]:
            """Extract bid/ask/bidq/askq from protobuf message."""
            valmap: dict[str, Any] = {}
            for fd, v in m.ListFields():
                name = fd.name.lower()
                if hasattr(v, "DESCRIPTOR"):
                    inner = {f2.name.lower(): v2 for f2, v2 in v.ListFields()}
                    valmap[name] = inner
                else:
                    valmap[name] = v

            def f(x):
                """Convert value to float."""
                if x is None or hasattr(x, "DESCRIPTOR"):
                    return None
                try:
                    return float(x)
                except Exception:
                    try:
                        if isinstance(x, (bytes, bytearray)):
                            return float(x.decode("utf-8", "ignore"))
                    except Exception:
                        pass
                return None

            # Field name aliases
            aliases = {
                "bid": ["bidprice", "bid_price", "bestbidprice"],
                "bidq": ["bidquantity", "bid_quantity", "bidqty", "bestbidqty", "bestbidquantity"],
                "ask": ["askprice", "ask_price", "bestaskprice"],
                "askq": ["askquantity", "ask_quantity", "askqty", "bestaskqty", "bestaskquantity"],
            }

            def pick(keys):
                """Pick first matching field."""
                for k in keys:
                    if k in valmap:
                        v = f(valmap[k])
                        if v is not None:
                            return v
                # Check nested dicts
                for k, v in valmap.items():
                    if isinstance(v, dict):
                        for cand in keys:
                            if cand in v:
                                fv = f(v[cand])
                                if fv is not None:
                                    return fv
                return None

            bid = pick(aliases["bid"])
            ask = pick(aliases["ask"])
            bidq = pick(aliases["bidq"])
            askq = pick(aliases["askq"])

            # Fallback: search for fields containing both keywords
            def contains(a, b):
                for k, v in valmap.items():
                    if isinstance(v, dict):
                        for kk, vv in v.items():
                            if a in kk and b in kk:
                                fv = f(vv)
                                if fv is not None:
                                    return fv
                    else:
                        if a in k and b in k:
                            fv = f(v)
                            if fv is not None:
                                return fv
                return None

            if bid is None:
                bid = contains("bid", "price")
            if ask is None:
                ask = contains("ask", "price")
            if bidq is None:
                bidq = contains("bid", "quant")
            if askq is None:
                askq = contains("ask", "quant")

            if bid is not None and ask is not None:
                return bid, (bidq or 0.0), ask, (askq or 0.0)

            # Sometimes first level embedded in repeated submessage
            try:
                desc = getattr(m, "DESCRIPTOR", None)
                if desc:
                    for fdesc in desc.fields:
                        if fdesc.label == fdesc.LABEL_REPEATED and fdesc.message_type:
                            arr = getattr(m, fdesc.name, [])
                            if not arr:
                                continue
                            first = arr[0]
                            names = {fd.name.lower() for fd, _ in first.ListFields()}
                            if "price" in names and ("quantity" in names or "qty" in names):
                                levels = []
                                for it in arr:
                                    nm = {fd.name.lower(): v for fd, v in it.ListFields()}
                                    px = f(nm.get("price"))
                                    qy = f(nm.get("quantity")) or f(nm.get("qty"))
                                    if px is not None and qy is not None:
                                        levels.append((px, qy))
                                if "bid" in fdesc.name.lower() and levels:
                                    bid, bidq = levels[0]
                                if "ask" in fdesc.name.lower() and levels:
                                    ask, askq = levels[0]
                if bid is not None and ask is not None:
                    return bid, (bidq or 0.0), ask, (askq or 0.0)
            except Exception:
                pass
            return None

        # Try primary class first
        if primary_cls:
            m = primary_cls()
            try:
                m.ParseFromString(data_bytes)
                if m.ListFields():
                    got = try_extract(m)
                    if got:
                        b, bq, a, aq = got
                        if self._quote_logger.accept_and_log(
                            symbol, b, bq, a, aq, send_time, verbose=self._verbose_frames
                        ):
                            self._total_book_tickers += 1
                            self._on_tick_metrics(send_time, symbol=symbol)
                            if not self._want_stop:
                                asyncio.create_task(_bt_cb(symbol, b, float(bq), a, float(aq), ts_ms=send_time))
                        return
            except Exception as e:
                logger.debug(f"Error parsing with primary class: {e}")

        # Bruteforce fallback
        if self._enable_bruteforce:
            bf = bruteforce_decode_book(data_bytes, self._book_modules)
            if bf is not None:
                mod_name, typ_name, m2 = bf
                got = try_extract(m2)
                if got:
                    b, bq, a, aq = got
                    if self._quote_logger.accept_and_log(
                        symbol, b, bq, a, aq, send_time, src=f"{mod_name}.{typ_name}", verbose=self._verbose_frames
                    ):
                        self._total_book_tickers += 1
                        self._on_tick_metrics(send_time, symbol=symbol)
                        if not self._want_stop:
                            asyncio.create_task(_bt_cb(symbol, b, float(bq), a, float(aq), ts_ms=send_time))
                    return

        if self._verbose_frames:
            logger.warning(f"🟡 bookTicker message did not contain recognizable fields for {symbol}")

    def _on_tick_metrics(self, send_time_ms: Optional[int], *, symbol: Optional[str] = None) -> None:
        """Update metrics and health after receiving a tick."""
        _metric_inc(ticks_total, symbol=symbol or "unknown", type="book_ticker")
        
        if send_time_ms:
            now_ms = _now_ms()
            lag_sec = max(0.0, (now_ms - int(send_time_ms)) / 1000.0)
            _metric_observe(ws_lag_seconds, lag_sec, symbol=symbol or "unknown")
            
        _health_tick()

    def _on_deals(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        """Parse and process deals (trades) message."""
        if self._want_stop:
            return

        data_bytes, _ = maybe_gunzip(data_bytes)
        cls = self._deals_cls

        if cls is None:
            logger.debug("🟡 No suitable deals class found — skipping processing")
            return
        
        logger.debug(f"➡️ Processing deals for {symbol}: raw len={len(data_bytes)}")

        try:
            msg = cls()
            msg.ParseFromString(data_bytes)
            
            # Try to find deals list in message
            publicdeals = getattr(msg, "publicdeals", None)
            if publicdeals is None:
                trades_list = (
                    getattr(msg, "deals", [])
                    or getattr(msg, "aggreDeals", [])
                    or getattr(msg, "data", [])
                    or getattr(msg, "trades", [])
                    or getattr(msg, "dealsList", [])
                    or getattr(msg, "tradesList", [])
                )
            else:
                trades_list = getattr(publicdeals, "dealsList", [])

            if self._verbose_frames:
                logger.debug(f"Received deals for {symbol}: {len(trades_list)} trades")

            # Calculate metrics
            recent_usd = 0.0
            cnt = 0
            now_sec = time.time()
            trades: List[Tuple[float, float, int]] = []
            
            for trade in trades_list:
                ts_ms = int(getattr(trade, "time", 0))
                ts = ts_ms / 1000
                
                # Only consider trades from last 60 seconds
                if now_sec - ts > 60:
                    continue
                    
                price_str = getattr(trade, "price", "0")
                qty_str = getattr(trade, "quantity", "0")
                
                try:
                    price = float(price_str)
                    qty = float(qty_str)
                except (ValueError, TypeError):
                    continue
                    
                usd = price * qty
                recent_usd += usd
                cnt += 1
                trades.append((price, qty, ts_ms))

            usdpm = recent_usd
            tpm = float(cnt)

            self._total_deals += 1
            self._on_tick_metrics(send_time, symbol=symbol)
            _metric_inc(ticks_total, symbol=symbol or "unknown", type="deals")

            # Update tape metrics asynchronously
            asyncio.create_task(self._update_live_tape(symbol, usdpm, tpm, trades))

            if self._verbose_frames:
                logger.debug(
                    f"📊 {symbol} deals: usdpm={usdpm:.1f}, tpm={tpm:.1f}, "
                    f"ts={send_time}, trades_len={len(trades)}"
                )

        except Exception as e:
            logger.error(f"❌ deals decode error for {symbol}: {e}", exc_info=self._verbose_frames)

    async def _update_live_tape(
        self, symbol: str, usdpm: float, tpm: float, trades: List[Tuple[float, float, int]]
    ) -> None:
        """Update tape metrics in book tracker."""
        try:
            await update_tape_metrics(symbol, usdpm, tpm, trades)
        except Exception as e:
            if self._verbose_frames:
                logger.warning(f"Update tape metrics failed for {symbol}: {e}")

    def _on_depth(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        """Parse and process depth (orderbook snapshot) message."""
        if self._want_stop:
            return
            
        cls = self._depth_cls or (DepthModule and find_depth_cls(DepthModule))
        if cls is None:
            if self._verbose_frames:
                logger.warning("🟡 No suitable depth message found in PublicLimitDepthsV3Api_pb2")
            return
            
        try:
            msg = cls()
            msg.ParseFromString(data_bytes)

            bids: list[tuple[float, float]] = []
            asks: list[tuple[float, float]] = []
            
            # Extract bids and asks from repeated fields
            for fdesc in msg.DESCRIPTOR.fields:
                if fdesc.label != fdesc.LABEL_REPEATED or not fdesc.message_type:
                    continue
                    
                arr = getattr(msg, fdesc.name, [])
                if not arr:
                    continue
                    
                first = arr[0]
                names = {fd.name.lower() for fd, _ in first.ListFields()}
                
                if {"price", "quantity"}.issubset(names) or {"price", "qty"}.issubset(names):
                    lvls = []
                    for it in arr:
                        nm = {fd.name.lower(): v for fd, v in it.ListFields()}
                        px = float(nm.get("price", 0.0))
                        qy = float(nm.get("quantity", nm.get("qty", 0.0)))
                        if px > 0 and qy > 0:
                            lvls.append((px, qy))
                            
                    if "bid" in fdesc.name.lower():
                        bids = lvls[:10]
                    elif "ask" in fdesc.name.lower():
                        asks = lvls[:10]

            if bids or asks:
                self._total_depth_updates += 1
                
                if self._verbose_frames:
                    sum5_bid = sum(p * q for p, q in bids[:5]) if bids else 0
                    sum5_ask = sum(p * q for p, q in asks[:5]) if asks else 0
                    logger.debug(
                        f"Depth update {symbol}: bids={len(bids)} (sum5=${sum5_bid:.0f}), "
                        f"asks={len(asks)} (sum5=${sum5_ask:.0f})"
                    )
                    
                asyncio.create_task(_depth_cb(symbol, bids, asks, ts_ms=send_time))
                
        except Exception as e:
            if self._verbose_frames:
                logger.error(f"❌ depth decode error for {symbol}: {e}", exc_info=True)

    # ───────────── misc ─────────────
    def _next_id(self) -> int:
        """Get next message ID."""
        self._id_counter += 1
        return self._id_counter

    async def _send_json(self, payload: dict) -> None:
        """Send JSON message to WebSocket."""
        if not self._ws or self._want_stop:
            return
        try:
            await self._ws.send(json.dumps(payload, separators=(",", ":")))
        except Exception as e:
            logger.warning(f"Error sending JSON: {e}")

    async def _reconnect_sleep(self) -> None:
        """Sleep with exponential backoff before reconnecting."""
        if self._want_stop:
            return
            
        delay = min(self._reconnect_delay * 2, self._reconnect_ceil)
        jitter = random.uniform(0, 0.25 * delay)
        self._reconnect_delay = max(self._reconnect_floor, delay + jitter)
        
        logger.info(f"🔄 Reconnecting in {self._reconnect_delay:.2f}s...")
        
        try:
            await asyncio.sleep(self._reconnect_delay)
        except asyncio.CancelledError:
            pass

    # ───────────── diagnostics ─────────────
    def get_stats(self) -> dict[str, Any]:
        """Get client statistics for monitoring."""
        return {
            "connected": self._connected,
            "symbols": len(self.symbols),
            "subscribed_topics": len(self._subscribed_topics),
            "total_reconnects": self._total_reconnects,
            "total_messages": self._total_messages_received,
            "total_book_tickers": self._total_book_tickers,
            "total_deals": self._total_deals,
            "total_depth_updates": self._total_depth_updates,
            "blocked_seen": self._blocked_seen,
            "downgraded": self._downgraded_once,
            "connection_age_sec": (
                (_now_ms() - self._started_at_ms) / 1000 if self._started_at_ms else 0
            ),
            "last_recv_age_sec": (
                (_now_ms() - self._last_recv_ts_ms) / 1000 if self._last_recv_ts_ms else 0
            ),
        }


__all__ = ["MEXCWebSocketClient", "GateWebSocketClient"]


if __name__ == "__main__":
    # Setup logging for standalone run
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    syms: Iterable[str] = getattr(settings, "symbols", []) or []
    client = MEXCWebSocketClient(
        list(syms), 
        channels=["BOOK_TICKER", "DEALS", "DEPTH_LIMIT"]
    )
    
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        pass