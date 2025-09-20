from __future__ import annotations
import asyncio, json, random, sys, time, socket, ssl
from typing import Iterable, List, Optional, Dict

import websockets
from app.config.settings import settings

# Fallback constants
try:
    from app.config.constants import (
        WS_MAX_TOPICS, WS_PING_INTERVAL_SEC, WS_MAX_LIFETIME_SEC,
        WS_PUBLIC_ENDPOINT, WS_CHANNELS, WS_RATE_SUFFIX,
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

# Service callbacks
try:
    from app.services.book_tracker import on_book_ticker as _bt_cb, on_partial_depth as _depth_cb
except Exception:
    from app.services.book_tracker import book_tracker as _book_tracker
    async def _bt_cb(symbol: str, bid: float, bid_qty: float, ask: float, ask_qty: float, ts_ms: Optional[int]):
        await _book_tracker.set_quote(symbol, last=None, bid=bid, ask=ask)  # type: ignore
    async def _depth_cb(symbol: str, bids, asks, ts_ms: Optional[int]): return

# Protobuf modules
PROTO_AVAILABLE = False
EnvelopeModule = None
BookTickerModule = None
AggreBookTickerModule = None
DepthModule = None
try:
    from app.market_data.mexc_pb import (
        PushDataV3ApiWrapper_pb2 as EnvelopeModule,
        PublicBookTickerV3Api_pb2 as BookTickerModule,
        PublicAggreBookTickerV3Api_pb2 as AggreBookTickerModule,
        PublicLimitDepthsV3Api_pb2 as DepthModule,
    )
    PROTO_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Protobuf decoders not available: {e}", file=sys.stderr)
    PROTO_AVAILABLE = False
    EnvelopeModule = None

# Helpers
from .ws_frame import hexdump, maybe_gunzip, extract_frames
from .proto_helpers import (
    iter_message_fields, first_set_fields_dict, debug_envelope_shape,
    find_book_ticker_cls, find_depth_cls, collect_bytes_candidates,
    bruteforce_decode_book,
)

def _now_ms() -> int: return int(time.time() * 1000)

class MEXCWebSocketClient:
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
        self.channels = channels or [WS_CHANNELS.get("BOOK_TICKER", "spot@public.aggre.bookTicker.v3.api.pb")]
        self.rate_suffix = rate_suffix
        self.ws_url = getattr(settings, "ws_url_public", None) or WS_PUBLIC_ENDPOINT

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

        # cached classes
        self._book_ticker_cls: Optional[type] = None
        self._depth_cls: Optional[type] = None
        self._book_modules = [m for m in (AggreBookTickerModule, BookTickerModule) if m]

        # logging/throttle
        self._last_log_by_symbol: Dict[str, int] = {}

        # debug toggles
        self._dbg_json_parity = bool(getattr(settings, "ws_debug_json_parity", False))
        self._dbg_pb_variants = bool(getattr(settings, "ws_debug_pb_variants", False))
        self._verbose_frames = bool(getattr(settings, "ws_verbose_frames", False))
        self._verbose_hexdump = bool(getattr(settings, "ws_verbose_hexdump", False))
        self._enable_bruteforce = bool(getattr(settings, "ws_enable_bruteforce", False))
        self._log_throttle_ms: int = int(getattr(settings, "ws_log_throttle_ms", 1000))

    async def run(self) -> None:
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            print("❌ Protobuf decoders not available — cannot run WS client.", file=sys.stderr)
            return
        if not self.symbols:
            print("⚠️ No symbols to subscribe — WS client not started.", file=sys.stderr)
            return
        self._want_stop = False
        while not self._want_stop:
            try:
                await self._connect()
                await self._subscribe_all()
                await self._listen_loop()
            except Exception as e:
                print(f"❌ WS loop error: {e}", file=sys.stderr)
            await self._reconnect_sleep()

    async def stop(self) -> None:
        self._want_stop = True
        try:
            if self._ws and self._connected and self._subscribed_topics:
                for t in list(self._subscribed_topics):
                    await self._send_json({"method": "UNSUBSCRIPTION", "params": [t], "id": self._next_id()})
                    await asyncio.sleep(0.05)
        except Exception:
            pass
        try:
            if self._ws: await self._ws.close()
        except Exception:
            pass
        self._connected = False

    async def _connect(self) -> None:
        print(f"🔌 Connecting to {self.ws_url} ...")
        # proxy env debug
        try:
            http_p = getattr(settings, "http_proxy_env", "")
            https_p = getattr(settings, "https_proxy_env", "")
            no_p = getattr(settings, "no_proxy_env", "")
            if any([http_p, https_p, no_p]):
                print(f"🧭 PROXY envs: HTTP_PROXY={bool(http_p)} HTTPS_PROXY={bool(https_p)} NO_PROXY={no_p or ''}")
        except Exception:
            pass

        host = self.ws_url.split('://',1)[-1].split('/',1)[0]
        path = self.ws_url.split(host,1)[-1]

        # DNS pre-resolve
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addrs = [f"{ai[0]}:{ai[4][0]}" for ai in infos]
            print(f"🔎 DNS {host} → {addrs or '[]'}")
        except Exception as e:
            print(f"🟡 DNS resolve error for {self.ws_url}: {e}")

        ws_uri = self.ws_url
        server_hostname = getattr(settings, "ws_server_hostname", host)
        ws_dns_override = (getattr(settings, "ws_dns_override", "") or "").strip()
        ssl_ctx = None
        server_hostname_kw = {}
        if ws_dns_override:
            ip = ws_dns_override
            ws_uri = f"wss://{ip}{path}"
            ssl_ctx = ssl.create_default_context()
            server_hostname_kw = {"server_hostname": server_hostname}
            print(f"🛠️ WS_DNS_OVERRIDE active: connecting to {ip} with SNI={server_hostname}")

        connect_kwargs = dict(
            ping_interval=None, ping_timeout=None, max_size=None,
            open_timeout=getattr(settings, "ws_open_timeout", 20),
            close_timeout=getattr(settings, "ws_close_timeout", 5),
        )
        if ssl_ctx is not None: connect_kwargs["ssl"] = ssl_ctx
        connect_kwargs.update(server_hostname_kw)

        self._ws = await websockets.connect(ws_uri, **connect_kwargs)
        self._connected = True
        self._last_recv_ts_ms = _now_ms()
        self._last_ping_ts_ms = 0
        self._reconnect_delay = self._reconnect_floor
        self._subscribed_topics.clear()
        self._started_at_ms = self._last_recv_ts_ms
        print("✅ Connected")

        # resolve classes
        if self._book_ticker_cls is None:
            for mod in self._book_modules:
                self._book_ticker_cls = find_book_ticker_cls(mod)
                if self._book_ticker_cls: break
            if self._book_ticker_cls is None:
                print("🟡 No suitable book-ticker class found in any module.")
        if DepthModule and self._depth_cls is None:
            self._depth_cls = find_depth_cls(DepthModule)

    async def _subscribe_all(self) -> None:
        assert self._ws and self._connected
        topics: list[str] = []
        for sym in self.symbols:
            for ch in self.channels:
                topics.append(f"{ch}{self.rate_suffix}@{sym}")
            if self._dbg_json_parity:
                topics.append(f"spot@public.aggre.bookTicker.v3.api{self.rate_suffix}@{sym}")
            if self._dbg_pb_variants:
                topics.append(f"spot@public.bookTicker.v3.api.pb{self.rate_suffix}@{sym}")

        if len(topics) > self.MAX_TOPICS_PER_CONN:
            raise RuntimeError(f"Too many topics ({len(topics)}) for a single WS. Shard needed.")

        for t in topics:
            await self._send_json({"method": "SUBSCRIPTION", "params": [t], "id": self._next_id()})
            print(f"📡 SUBSCRIBE → {t}")
            await asyncio.sleep(0.15)

    async def _listen_loop(self) -> None:
        assert self._ws and self._connected
        ws = self._ws
        while not self._want_stop:
            now = _now_ms()
            if now - self._last_recv_ts_ms > WS_PING_INTERVAL_SEC * 1000 and now - self._last_ping_ts_ms > WS_PING_INTERVAL_SEC * 1000:
                await self._send_json({"method": "PING", "id": self._next_id()})
                self._last_ping_ts_ms = now
                print("↪️ JSON PING sent")

            if now - self._started_at_ms > WS_MAX_LIFETIME_SEC * 1000:
                print("♻️ Max lifetime reached — cycling connection"); break

            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed as e:
                print(f"❌ WebSocket closed: {e}"); break

            self._last_recv_ts_ms = _now_ms()

            if isinstance(message, (bytes, bytearray)):
                if self._verbose_frames:
                    try: print(f"🔹 WS frame: type=bytes len={len(message)} head={hexdump(message)}")
                    except Exception: pass
                await self._handle_binary(message)
            else:
                await self._handle_text(message)
        self._connected = False

    async def _handle_text(self, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            if self._verbose_frames: print(f"🟡 Text (non-JSON?) {message[:200]}")
            return
        if "code" in data and "msg" in data:
            code = data.get("code"); msg = data.get("msg")
            if code == 0:
                print(f"✅ ACK code=0 msg={msg}")
                if isinstance(msg, str) and msg.startswith("spot@"):
                    self._subscribed_topics.add(msg)
            else:
                print(f"❗ ACK error code={code} msg={msg}")
            return
        if data.get("ping") is not None or data.get("pong") is not None:
            if self._verbose_frames: print(f"🫧 Heartbeat ack: {data}")
            return
        if self._verbose_frames: print(f"ℹ️ Service message: {data}")

    async def _handle_binary(self, payload: bytes) -> None:
        if not PROTO_AVAILABLE or EnvelopeModule is None:
            print("🟡 Binary frame but protobuf env not available."); return

        payload, was_gz = maybe_gunzip(payload)
        if self._verbose_hexdump and was_gz:
            try: print(f"🗜️ gunzipped payload len={len(payload)} head={hexdump(payload)}")
            except Exception: pass

        try:
            desc = getattr(EnvelopeModule, "DESCRIPTOR", None)
            if not desc:
                print("🟡 Envelope module without DESCRIPTOR."); return
        except Exception as e:
            print(f"❌ Protobuf parse error: {e}"); return

        parsed_any = False
        dbg = (lambda s: print(s)) if self._verbose_frames else None

        for typ in EnvelopeModule.DESCRIPTOR.message_types_by_name.values():
            try_cls = getattr(EnvelopeModule, typ.name, None)
            if try_cls is None: continue

            msg = try_cls()
            try: msg.ParseFromString(payload)
            except Exception: continue

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
                if self._verbose_frames:
                    print(f"🧵 Frame extracted: ch={ch_str} sym={sym or ''} ts={int(ts)} bytes={len(data_bytes)}")
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

    # ——— Parsers ———
    def _log_parsed(self, symbol: str, bid: float, bidq: float, ask: float, askq: float, src: Optional[str] = None, lag_ms: Optional[int] = None) -> None:
        now = _now_ms()
        last = self._last_log_by_symbol.get(symbol, 0)
        if now - last >= self._log_throttle_ms:
            lag_str = f" lag={lag_ms}ms" if lag_ms is not None else ""
            if src: print(f"✅ Parsed {symbol} via {src}: bid={bid} ({bidq}) ask={ask} ({askq}){lag_str}")
            else:   print(f"✅ Parsed {symbol}: bid={bid} ({bidq}) ask={ask} ({askq}){lag_str}")
            # quick mid/spread summary
            mid = (bid + ask) / 2.0
            spread = max(0.0, ask - bid)
            bps = (spread / mid * 1e4) if mid > 0 else 0.0
            print(f"📈 {symbol} mid={mid:.8f} spread={spread:.8f} ({bps:.2f} bps) | sizes: bid={bidq} ask={askq}")
            self._last_log_by_symbol[symbol] = now

    def _on_book_ticker(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        try:
            # primary parse
            primary_cls = self._book_ticker_cls
            if primary_cls:
                m = primary_cls(); m.ParseFromString(data_bytes)
                if m.ListFields():
                    # scalar aliases
                    valmap = {}
                    for fd, v in m.ListFields():
                        name = fd.name.lower()
                        if hasattr(v, "DESCRIPTOR"):
                            valmap[name] = {f2.name.lower(): v2 for f2, v2 in v.ListFields()}
                        else:
                            valmap[name] = v

                    def f(x):
                        if x is None or hasattr(x, "DESCRIPTOR"): return None
                        try: return float(x)
                        except Exception:
                            try:
                                if isinstance(x, (bytes, bytearray)):
                                    return float(x.decode("utf-8","ignore"))
                            except Exception: pass
                        return None

                    aliases = {
                        "bid":  ["bidprice","bid_price","bestbidprice"],
                        "bidq": ["bidquantity","bid_quantity","bidqty","bestbidqty","bestbidquantity"],
                        "ask":  ["askprice","ask_price","bestaskprice"],
                        "askq": ["askquantity","ask_quantity","askqty","bestaskqty","bestaskquantity"],
                    }
                    def pick(keys):
                        for k in keys:
                            if k in valmap:
                                v = f(valmap[k])
                                if v is not None: return v
                        for k, v in valmap.items():
                            if isinstance(v, dict):
                                for cand in keys:
                                    if cand in v:
                                        fv = f(v[cand])
                                        if fv is not None: return fv
                        return None

                    bid  = pick(aliases["bid"])
                    ask  = pick(aliases["ask"])
                    bidq = pick(aliases["bidq"])
                    askq = pick(aliases["askq"])

                    # fallbacks (depth-like) — на практике для aggre ticker не требуется
                    if bid is not None and ask is not None:
                        lag_ms = max(0, _now_ms() - int(send_time)) if send_time else None
                        self._log_parsed(symbol, bid, (bidq or 0.0), ask, (askq or 0.0), lag_ms=lag_ms)
                        asyncio.create_task(_bt_cb(symbol, float(bid), float(bidq or 0.0), float(ask), float(askq or 0.0), ts_ms=send_time))
                        return

                # verbose field dump
                # print("🧩", first_set_fields_dict(m))
            # optional bruteforce
            if self._enable_bruteforce and self._book_modules:
                bf = bruteforce_decode_book(data_bytes, self._book_modules)
                if bf is not None:
                    mod_name, typ_name, m2 = bf
                    # повторно извлечём как выше
                    valmap = {}
                    for fd, v in m2.ListFields():
                        name = fd.name.lower()
                        if hasattr(v, "DESCRIPTOR"):
                            valmap[name] = {f2.name.lower(): v2 for f2, v2 in v.ListFields()}
                        else:
                            valmap[name] = v
                    def f(x):
                        if x is None or hasattr(x, "DESCRIPTOR"): return None
                        try: return float(x)
                        except Exception:
                            try:
                                if isinstance(x, (bytes, bytearray)):
                                    return float(x.decode("utf-8","ignore"))
                            except Exception: pass
                        return None
                    bid  = next((f(valmap[k]) for k in ("bidprice","bid_price") if k in valmap), None)
                    ask  = next((f(valmap[k]) for k in ("askprice","ask_price") if k in valmap), None)
                    bidq = next((f(valmap[k]) for k in ("bidquantity","bid_quantity","bidqty") if k in valmap), None)
                    askq = next((f(valmap[k]) for k in ("askquantity","ask_quantity","askqty") if k in valmap), None)
                    if bid is not None and ask is not None:
                        lag_ms = max(0, _now_ms() - int(send_time)) if send_time else None
                        self._log_parsed(symbol, bid, (bidq or 0.0), ask, (askq or 0.0), src=f"{mod_name}.{typ_name}", lag_ms=lag_ms)
                        asyncio.create_task(_bt_cb(symbol, float(bid), float(bidq or 0.0), float(ask), float(askq or 0.0), ts_ms=send_time))
                        return
        except Exception as e:
            print(f"❌ bookTicker decode error: {e}")

    def _on_deals(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        try:
            # placeholder — можно расширить при необходимости
            if self._verbose_frames:
                print(f"📨 deals {symbol} bytes={len(data_bytes)} ts={int(send_time)}")
        except Exception as e:
            if self._verbose_frames:
                print(f"❌ deals decode error: {e}")

    def _on_depth(self, symbol: str, data_bytes: bytes, send_time: int) -> None:
        try:
            cls = self._depth_cls or (DepthModule and find_depth_cls(DepthModule))
            if cls is None:
                if self._verbose_frames:
                    print("🟡 No suitable depth message found in PublicLimitDepthsV3Api_pb2.")
                return
            msg = cls(); msg.ParseFromString(data_bytes)

            bids: list[tuple[float, float]] = []
            asks: list[tuple[float, float]] = []
            for fname, _, is_rep in iter_message_fields(msg):
                if not is_rep: continue
                field_desc = msg.DESCRIPTOR.fields_by_name.get(fname)
                if not (field_desc and field_desc.message_type): continue
                elem_desc = field_desc.message_type
                elem_fields = {f.name for f in elem_desc.fields}
                if {"price", "quantity"}.issubset(elem_fields):
                    levels = [(float(getattr(l, "price", 0.0)), float(getattr(l, "quantity", 0.0))) for l in getattr(msg, fname)]
                    if "bid" in fname.lower(): bids = levels[:10]
                    elif "ask" in fname.lower(): asks = levels[:10]

            if bids or asks:
                asyncio.create_task(_depth_cb(symbol, bids, asks, ts_ms=send_time))
        except Exception as e:
            if self._verbose_frames:
                print(f"❌ depth decode error: {e}")

    # ——— utils ———
    def _next_id(self) -> int:
        self._id_counter += 1; return self._id_counter

    async def _send_json(self, payload: dict) -> None:
        if not self._ws: return
        await self._ws.send(json.dumps(payload, separators=(",", ":")))

    async def _reconnect_sleep(self) -> None:
        if self._want_stop: return
        delay = min(self._reconnect_delay * 2, self._reconnect_ceil)
        jitter = random.uniform(0, 0.25 * delay)
        self._reconnect_delay = max(self._reconnect_floor, delay + jitter)
        print(f"🔄 Reconnecting in {self._reconnect_delay:.2f}s ...")
        await asyncio.sleep(self._reconnect_delay)

if __name__ == "__main__":
    syms: Iterable[str] = getattr(settings, "symbols", []) or []
    client = MEXCWebSocketClient(list(syms))
    asyncio.run(client.run())
