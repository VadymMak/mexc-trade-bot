# app/market_data/ws_client.py
from __future__ import annotations

import asyncio
import json
import random
import socket
import ssl
import sys
import time
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

# ✅ Use the single, canonical Gate client
from app.market_data.gate_ws import GateWebSocketClient

# ── metrics & health (minimal integration) ──────────────────────────────────
try:
    from app.infra.metrics import (
        ticks_total,
        ws_lag_seconds,
        ws_reconnects_total,
        ws_active_subscriptions,
    )
except Exception:  # soft-fail if metrics module not present
    ticks_total = None
    ws_lag_seconds = None
    ws_reconnects_total = None
    ws_active_subscriptions = None

try:
    from app.infra.health import ws_health
except Exception:  # soft-fail if health module not present
    ws_health = None

# ── safe constants import ───────────────────────────────────────────────────
try:
    from app.config.constants import (
        WS_MAX_TOPICS,
        WS_PING_INTERVAL_SEC,
        WS_MAX_LIFETIME_SEC,
        WS_PUBLIC_ENDPOINT,
        WS_CHANNELS,
        WS_RATE_SUFFIX,
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
    WS_RATE_SUFFIX = "@100ms"

# ── service callbacks ───────────────────────────────────────────────────────
try:
    from app.services.book_tracker import (
        on_book_ticker as _bt_cb,
        on_partial_depth as _depth_cb,
        update_tape_metrics,  # live usdpm/tpm + trades
    )
except Exception:
    # Fallback stubs (keep app running in smoke tests without full tracker)
    from app.services.book_tracker import book_tracker as _book_tracker

    async def _bt_cb(
        symbol: str,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
        ts_ms: Optional[int],
    ):
        print(f"📊 Book ticker stub for {symbol}: bid={bid:.4f}, ask={ask:.4f}, ts_ms={ts_ms}")

    async def _depth_cb(
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        ts_ms: Optional[int],
    ):
        return

    async def update_tape_metrics(
        symbol: str,
        usdpm: float,
        tpm: float,
        trades: Optional[List[Tuple[float, float, int]]] = None,
    ):
        if usdpm > 0 or tpm > 0:
            print(f"Updated tape for {symbol}: usdpm={usdpm:.2f}, tpm={tpm:.1f}")
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
            print(
                f"Computed atr_proxy={atr:.2f} for {symbol}, total trades now={len(_book_tracker.recent_trades[symbol])}"
            )

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
            print(f"Computed vol_pattern={vp} for {symbol} (ratio={ratio:.3f})")

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
except Exception as e:
    print(f"⚠️ Protobuf decoders not available: {e}", file=sys.stderr)
    PROTO_AVAILABLE = False
    EnvelopeModule = None


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── small metric helpers (don’t crash if metrics missing) ───────────────────
def _metric_inc(counter, **labels) -> None:
    if counter is None:
        return
    try:
        if hasattr(counter, "labels") and labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        pass


def _metric_set(gauge, value: float) -> None:
    if gauge is None:
        return
    try:
        gauge.set(value)
    except Exception:
        pass


def _metric_observe(hist, value: float, **labels) -> None:
    if hist is None:
        return
    try:
        if hasattr(hist, "labels") and labels:
            hist.labels(**labels).observe(value)
        else:
            hist.observe(value)
    except Exception:
        pass


def _health_started() -> None:
    try:
        if ws_health:
            ws_health.mark_started()
    except Exception:
        pass


def _health_tick() -> None:
    try:
        if ws_health:
            ws_health.mark_tick()
    except Exception:
        pass


def _health_stopped() -> None:
    try:
        if ws_health:
            ws_health.mark_stopped()
    except Exception:
        pass


def _resolve_channels(channels: Optional[List[str]]) -> List[str]:
    """
    Accept either channel KEYS (e.g. 'BOOK_TICKER') or full topics
    (e.g. 'spot@public.limit.depth.v3.api.pb'). Return normalized topics.
    Fallback to older channel naming when seen (2025 updates).
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
            print(f"🔄 Fallback: mapped old '{orig}' to new '{mapped}'")
        if ch.startswith("spot@"):
            out.append(ch)
        else:
            mapped = WS_CHANNELS.get(ch)
            out.append(mapped if mapped else ch)
    return out


class MEXCWebSocketClient:
    """
    Public MEXC Spot WS client (v3):
      • Subscribes to book-ticker, deals, and depth topics (protobuf frames).
      • Normalizes events and forwards to book_tracker.
      • Resilient reconnects with jitter/backoff & soft heartbeats.
    """
    MAX_TOPICS_PER_CONN = WS_MAX_TOPICS

    def __init__(
        self,
        symbols: List[str],
        channels: Optional[List[str]] = None,
        rate_suffix: str = WS_RATE_SUFFIX,
        reconnect_floor: float = 0.5,
        reconnect_ceil: float = 30.0,
    ):
        self.symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        self.channels = _resolve_channels(channels)
        self.rate_suffix = rate_suffix
        self.ws_url = (
            getattr(settings, "ws_base_url_resolved", None)
            or getattr(settings, "ws_url_public", None)
            or WS_PUBLIC_ENDPOINT
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

    # ───────────── lifecycle ─────────────
    async def run(self) -> None:
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            print("❌ Protobuf decoders not available — cannot run WS client.", file=sys.stderr)
            return
        if not self.symbols:
            print("⚠️ No symbols to subscribe — WS client not started.", file=sys.stderr)
            return

        _health_started()

        self._want_stop = False
        try:
            while not self._want_stop:
                try:
                    await self._connect()
                    await self._subscribe_all()
                    await self._listen_loop()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self._want_stop:
                        break
                    print(f"❌ WS loop error: {e}", file=sys.stderr)
                await self._reconnect_sleep()
        except asyncio.CancelledError:
            pass
        finally:
            await self._graceful_close()
            _health_stopped()

    async def stop(self) -> None:
        self._want_stop = True
        try:
            if self._ws and self._connected and self._subscribed_topics:
                for t in list(self._subscribed_topics):
                    await self._send_json({"method": "UNSUBSCRIPTION", "params": [t], "id": self._next_id()})
                    await asyncio.sleep(0)
        except Exception:
            pass
        await self._graceful_close()

    async def _graceful_close(self) -> None:
        try:
            if self._ws:
                try:
                    await asyncio.wait_for(self._ws.close(), timeout=1.5)
                except asyncio.TimeoutError:
                    try:
                        self._ws.transport.close()  # type: ignore[attr-defined]
                    except Exception:
                        pass
        finally:
            self._ws = None
            self._connected = False
            self._subscribed_topics.clear()

    # ───────────── connect/subscribe/listen ─────────────
    async def _connect(self) -> None:
        print(f"🔌 Connecting to {self.ws_url} ...")

        # diagnostics: DNS
        try:
            host = self.ws_url.split("://", 1)[-1].split("/", 1)[0]
            path = self.ws_url.split(host, 1)[-1]
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addrs = [f"{ai[4][0]}" for ai in infos]
            print(f"🔎 DNS {host} → {addrs or '[]'}")
        except Exception as e:
            print(f"🟡 DNS resolve error for {self.ws_url}: {e}")
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
            print(f"🛠️ WS_DNS_OVERRIDE active: connecting to {ip} with SNI={server_hostname}")

        connect_kwargs = dict(
            ping_interval=None,  # we send JSON PINGs; server also sends its own heartbeats
            ping_timeout=None,
            max_size=None,
            open_timeout=getattr(settings, "ws_open_timeout", 20),
            close_timeout=getattr(settings, "ws_close_timeout", 5),
        )
        if ssl_ctx is not None:
            connect_kwargs["ssl"] = ssl_ctx
        connect_kwargs.update(server_hostname_kw)

        self._ws = await websockets.connect(ws_uri, **connect_kwargs)

        self._connected = True
        now = _now_ms()
        self._last_recv_ts_ms = now
        self._last_ping_ts_ms = 0
        self._reconnect_delay = self._reconnect_floor
        self._subscribed_topics.clear()
        self._started_at_ms = now
        print("✅ Connected")

        _metric_inc(ws_reconnects_total)

        # resolve protobuf classes once per connection (safe if re-run)
        if self._book_ticker_cls is None:
            for mod in [m for m in (AggreBookTickerModule, BookTickerModule) if m]:
                self._book_ticker_cls = find_book_ticker_cls(mod)
                if self._book_ticker_cls:
                    break
            if self._book_ticker_cls is None:
                print("🟡 No suitable book-ticker class found in any module.")
        if DepthModule and self._depth_cls is None:
            self._depth_cls = find_depth_cls(DepthModule)
        if DealsModule and self._deals_cls is None:
            self._deals_cls = getattr(DealsModule, "PublicAggreDealsV3Api", None)
            if self._deals_cls is None:
                for typ in DealsModule.DESCRIPTOR.message_types_by_name.values():
                    try_cls = getattr(DealsModule, typ.name, None)
                    # ✅ parentheses so 'or "public"' doesn’t bypass check
                    if try_cls and ("deals" in typ.name.lower() or "public" in typ.name.lower()):
                        self._deals_cls = try_cls
                        break
            if self._deals_cls is None:
                print("🟡 No suitable deals class found in PublicAggreDealsV3Api_pb2.")

    async def _subscribe_all(self) -> None:
        assert self._ws and self._connected
        topics: list[str] = []

        levels = int(getattr(settings, "ws_orderbook_snapshot_levels", 10))

        for sym in self.symbols:
            for ch in self.channels:
                ch_l = ch.lower()
                if (".bookticker." in ch_l) or (".aggre.depth." in ch_l) or (".deals." in ch_l):
                    topics.append(f"{ch}{self.rate_suffix}@{sym}")
                elif ".limit.depth." in ch_l:
                    topics.append(f"{ch}@{sym}@{levels}")
                else:
                    topics.append(f"{ch}{self.rate_suffix}@{sym}")

            # Optional parity/debug topics
            if self._dbg_json_parity:
                topics.append(f"spot@public.aggre.bookTicker.v3.api{self.rate_suffix}@{sym}")
            if self._dbg_pb_variants:
                topics.append(f"spot@public.bookTicker.v3.api.pb{self.rate_suffix}@{sym}")

        if len(topics) > self.MAX_TOPICS_PER_CONN:
            raise RuntimeError(f"Too many topics ({len(topics)}) for a single WS. Shard needed.")

        _metric_set(ws_active_subscriptions, float(len(topics)))

        for t in topics:
            await self._send_json({"method": "SUBSCRIPTION", "params": [t], "id": self._next_id()})
            print(f"📡 SUBSCRIBE → {t}")
            await asyncio.sleep(0)

    async def _listen_loop(self) -> None:
        assert self._ws and self._connected
        ws = self._ws

        while not self._want_stop:
            now = _now_ms()

            # JSON PING if idle
            if (
                now - self._last_recv_ts_ms > WS_PING_INTERVAL_SEC * 1000
                and now - self._last_ping_ts_ms > WS_PING_INTERVAL_SEC * 1000
            ):
                await self._send_json({"method": "PING", "id": self._next_id()})
                self._last_ping_ts_ms = now
                print("↪️ JSON PING sent")

            # Cycle connection periodically to avoid stale state
            if now - self._started_at_ms > WS_MAX_LIFETIME_SEC * 1000:
                print("♻️ Max lifetime reached — cycling connection")
                break

            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                if self._want_stop:
                    break
                continue
            except asyncio.CancelledError:
                break
            except websockets.exceptions.ConnectionClosed as e:
                print(f"❌ WebSocket closed: {e}")
                break
            except Exception as e:
                if self._want_stop:
                    break
                print(f"🟡 recv error: {e}")
                break

            self._last_recv_ts_ms = _now_ms()

            if isinstance(message, (bytes, bytearray)):
                if self._verbose_frames:
                    with suppress(Exception):
                        print(f"🔹 WS frame: type=bytes len={len(message)} head={hexdump(message)}")
                await self._handle_binary(message)
            else:
                await self._handle_text(message)

        self._connected = False

    # ───────────── handlers ─────────────
    async def _handle_text(self, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            if self._verbose_frames:
                print(f"🟡 Text (non-JSON?) {message[:200]}")
            return

        # ACKs
        if "code" in data and "msg" in data:
            code = data.get("code")
            msg = data.get("msg")
            if code == 0:
                print(f"✅ ACK code=0 msg={msg}")
                if isinstance(msg, str) and msg.startswith("spot@"):
                    self._subscribed_topics.add(msg)
            else:
                print(f"❗ ACK error code={code} msg={msg}")
            return

        # Heartbeats
        if data.get("ping") is not None or data.get("pong") is not None:
            if self._verbose_frames:
                print(f"🫧 Heartbeat ack: {data}")
            return

        if self._verbose_frames:
            print(f"ℹ️ Service message: {data}")

    async def _handle_binary(self, payload: bytes) -> None:
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            print("🟡 Binary frame but protobuf env not available.")
            return

        payload, was_gz = maybe_gunzip(payload)
        if self._verbose_hexdump and was_gz:
            with suppress(Exception):
                print(f"🗜️ gunzipped payload len={len(payload)} head={hexdump(payload)}")

        try:
            desc = getattr(EnvelopeModule, "DESCRIPTOR", None)
            if not desc:
                print("🟡 Envelope module without DESCRIPTOR.")
                return
        except Exception as e:
            print(f"❌ Protobuf parse error: {e}")
            return

        parsed_any = False
        dbg = (lambda s: print(s)) if self._verbose_frames else None

        for typ in EnvelopeModule.DESCRIPTOR.message_types_by_name.values():
            try_cls = getattr(EnvelopeModule, typ.name, None)
            if try_cls is None:
                continue

            msg = try_cls()
            try:
                msg.ParseFromString(payload)
            except Exception:
                continue

            frames = list(extract_frames(msg, debug_cb=dbg))
            if not frames:
                if self._verbose_frames:
                    debug_envelope_shape(msg)
                    cands = collect_bytes_candidates(msg)
                    if cands:
                        cands_sorted = sorted(cands, key=lambda kv: len(kv[1]), reverse=True)
                        top = ", ".join([f"{p}={len(b)}" for p, b in cands_sorted[:6]])
                        print(f"🧪 Wrapper bytes candidates (path=len): {top}")
                continue

            parsed_any = True
            for ch, sym, ts, data_bytes in frames:
                ch_str = str(ch)
                if "deals" in ch.lower():
                    # helpful signal during bringup
                    print(f"💥 Deals message arrived for {sym}: len(data_bytes)={len(data_bytes)}")
                if self._verbose_frames:
                    print(
                        f"🧵 Frame extracted: ch={ch_str} sym={sym or ''} ts={int(ts)} bytes={len(data_bytes)}"
                    )
                if "bookTicker" in ch_str:
                    self._on_book_ticker(sym or "", data_bytes, int(ts or 0))
                elif ".deals." in ch_str:
                    self._on_deals(sym or "", data_bytes, int(ts or 0))
                elif ".limit.depth" in ch_str or ".increase.depth" in ch_str or "Depth" in ch_str:
                    self._on_depth(sym or "", data_bytes, int(ts or 0))
                else:
                    if self._verbose_frames:
                        print(f"ℹ️ Unhandled channel: {ch_str} ({len(data_bytes)} bytes)")
            break

        if not parsed_any and self._verbose_frames:
            print("🟡 Protobuf envelope parsed but no frames found.")

    # ───────────── domain parsers ─────────────
    def _on_book_ticker(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        if self._want_stop:
            return

        data_bytes, _ = maybe_gunzip(data_bytes)
        primary_cls = self._book_ticker_cls

        def try_extract(m) -> Optional[tuple[float, float, float, float]]:
            valmap: dict[str, Any] = {}
            for fd, v in m.ListFields():
                name = fd.name.lower()
                if hasattr(v, "DESCRIPTOR"):
                    inner = {f2.name.lower(): v2 for f2, v2 in v.ListFields()}
                    valmap[name] = inner
                else:
                    valmap[name] = v

            def f(x):
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

            aliases = {
                "bid": ["bidprice", "bid_price", "bestbidprice"],
                "bidq": ["bidquantity", "bid_quantity", "bidqty", "bestbidqty", "bestbidquantity"],
                "ask": ["askprice", "ask_price", "bestaskprice"],
                "askq": ["askquantity", "ask_quantity", "askqty", "bestaskqty", "bestaskquantity"],
            }

            def pick(keys):
                for k in keys:
                    if k in valmap:
                        v = f(valmap[k])
                        if v is not None:
                            return v
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

            # sometimes first level embedded in repeated submessage
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
                            self._on_tick_metrics(send_time, symbol=symbol)
                            if not self._want_stop:
                                asyncio.create_task(_bt_cb(symbol, b, float(bq), a, float(aq), ts_ms=send_time))
                        return
            except Exception:
                pass

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
                        self._on_tick_metrics(send_time, symbol=symbol)
                        if not self._want_stop:
                            asyncio.create_task(_bt_cb(symbol, b, float(bq), a, float(aq), ts_ms=send_time))
                    return

        if self._verbose_frames:
            print(f"🟡 bookTicker message did not contain recognizable fields for {symbol}")

    def _on_tick_metrics(self, send_time_ms: Optional[int], *, symbol: Optional[str] = None) -> None:
        _metric_inc(ticks_total, symbol=symbol or "unknown", type="book_ticker")
        if send_time_ms:
            now_ms = _now_ms()
            lag_sec = max(0.0, (now_ms - int(send_time_ms)) / 1000.0)
            _metric_observe(ws_lag_seconds, lag_sec, symbol=symbol or "unknown")
        _health_tick()

    def _on_deals(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        if self._want_stop:
            return

        data_bytes, _ = maybe_gunzip(data_bytes)
        cls = self._deals_cls

        if cls is None:
            print("🟡 No suitable deals class found — skipping processing.")
            return
        print(f"➡️ Processing deals for {symbol}: raw len={len(data_bytes)}")

        try:
            msg = cls()
            msg.ParseFromString(data_bytes)
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
                print(f"Received deals for {symbol}: {len(trades_list)} trades")

            recent_usd = 0.0
            cnt = 0
            now_sec = time.time()
            trades: List[Tuple[float, float, int]] = []
            for trade in trades_list:
                ts_ms = int(getattr(trade, "time", 0))
                ts = ts_ms / 1000
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

            self._on_tick_metrics(send_time, symbol=symbol)
            _metric_inc(ticks_total, symbol=symbol or "unknown", type="deals")

            asyncio.create_task(self._update_live_tape(symbol, usdpm, tpm, trades))

            if self._verbose_frames:
                print(
                    f"📊 {symbol} deals: usdpm={usdpm:.1f}, tpm={tpm:.1f}, ts={send_time}, trades_len={len(trades)}"
                )

        except Exception as e:
            print(f"❌ deals decode error for {symbol}: {e}")

    async def _update_live_tape(
        self, symbol: str, usdpm: float, tpm: float, trades: List[Tuple[float, float, int]]
    ) -> None:
        try:
            await update_tape_metrics(symbol, usdpm, tpm, trades)
        except Exception as e:
            if self._verbose_frames:
                print(f"🟡 Update tape metrics failed for {symbol}: {e}")

    def _on_depth(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        if self._want_stop:
            return
        cls = self._depth_cls or (DepthModule and find_depth_cls(DepthModule))
        if cls is None:
            if self._verbose_frames:
                print("🟡 No suitable depth message found in PublicLimitDepthsV3Api_pb2.")
            return
        try:
            msg = cls()
            msg.ParseFromString(data_bytes)

            bids: list[tuple[float, float]] = []
            asks: list[tuple[float, float]] = []
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
                if self._verbose_frames:
                    sum5_bid = sum(p * q for p, q in bids[:5]) if bids else 0
                    sum5_ask = sum(p * q for p, q in asks[:5]) if asks else 0
                    print(f"Depth update: bids={len(bids)} (sum5={sum5_bid:.0f}), asks={len(asks)} (sum5={sum5_ask:.0f})")
                asyncio.create_task(_depth_cb(symbol, bids, asks, ts_ms=send_time))
        except Exception as e:
            if self._verbose_frames:
                print(f"❌ depth decode error: {e}")

    # ───────────── misc ─────────────
    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def _send_json(self, payload: dict) -> None:
        if not self._ws or self._want_stop:
            return
        try:
            await self._ws.send(json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass

    async def _reconnect_sleep(self) -> None:
        if self._want_stop:
            return
        delay = min(self._reconnect_delay * 2, self._reconnect_ceil)
        jitter = random.uniform(0, 0.25 * delay)
        self._reconnect_delay = max(self._reconnect_floor, delay + jitter)
        print(f"🔄 Reconnecting in {self._reconnect_delay:.2f}s ...")
        try:
            await asyncio.sleep(self._reconnect_delay)
        except asyncio.CancelledError:
            pass


__all__ = ["MEXCWebSocketClient", "GateWebSocketClient"]


if __name__ == "__main__":
    syms: Iterable[str] = getattr(settings, "symbols", []) or []
    client = MEXCWebSocketClient(list(syms), channels=["BOOK_TICKER", "DEALS", "DEPTH_LIMIT"])
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
