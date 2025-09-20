# tools/mexc_ws_probe.py
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import socket
import ssl
from typing import Optional, Iterable

import websockets


MEXC_WSS_HOST = "wbs-api.mexc.com"
MEXC_WSS_PATH = "/ws"


def gunzip_if_needed(b: bytes) -> bytes:
    if len(b) >= 2 and b[0] == 0x1F and b[1] == 0x8B:
        try:
            return gzip.decompress(b)
        except Exception:
            return b
    return b


def resolve_all(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addrs = []
        for fam, _, _, _, sa in infos:
            ip = sa[0]
            addrs.append(f"{2 if fam == socket.AF_INET else 23}:{ip}")
        # Keep only the ip part for connect attempts
        return [a.split(":")[1] for a in addrs]
    except Exception as e:
        print(f"🟡 DNS resolve error for {host}: {e}")
        return []


async def connect(ws_uri: str, sni: Optional[str], ip_override: Optional[str],
                  open_timeout: float, close_timeout: float):
    kw = dict(
        ping_interval=None,
        ping_timeout=None,
        max_size=None,
        open_timeout=open_timeout,
        close_timeout=close_timeout,
    )
    if ip_override:
        # override → need explicit SSL context + SNI
        ctx = ssl.create_default_context()
        kw["ssl"] = ctx
        kw["server_hostname"] = sni or MEXC_WSS_HOST
    return await websockets.connect(ws_uri, **kw)


async def probe(symbol: str, sni: Optional[str], override_ip: Optional[str], verbose: bool):
    host = MEXC_WSS_HOST
    path = MEXC_WSS_PATH

    # Build a candidate list of IPs to try if override was provided OR DNS is flaky
    ip_candidates: list[str] = []

    # If user asked for a single override, try it first
    if override_ip:
        print(f"🛠️ Using IP override {override_ip} with SNI={sni or host}")
        ip_candidates.append(override_ip)

    # Always resolve DNS to have a fallback list
    resolved = resolve_all(host)
    if resolved:
        print(f"🔎 DNS {host} → {resolved}")
        # append the resolved IPs (de-dup while preserving override first)
        for ip in resolved:
            if ip not in ip_candidates:
                ip_candidates.append(ip)

    # If we still have nothing, last resort is a direct hostname connect (may fail)
    if not ip_candidates and not override_ip:
        ip_candidates = [None]  # type: ignore

    # Try candidates one by one until success
    last_err: Optional[BaseException] = None
    for ip in ip_candidates:
        ws_uri = f"wss://{(ip or host)}{path}"
        try:
            print(f"🔌 Connecting → {ws_uri}")
            ws = await connect(ws_uri, sni or host, ip, open_timeout=20, close_timeout=5)
            try:
                # Subscribe to the public aggre bookTicker
                sub = {
                    "method": "SUBSCRIPTION",
                    "params": [f"spot@public.aggre.bookTicker.v3.api.pb@100ms@{symbol.upper()}"],
                    "id": 1,
                }
                await ws.send(json.dumps(sub))
                print("📡 SUBSCRIBE sent.")

                # Read a handful of frames, print summaries
                for _ in range(10):
                    m = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    if isinstance(m, (bytes, bytearray)):
                        b = gunzip_if_needed(m)
                        print(f"📦 binary frame: {len(b)} bytes (gz handled)")
                    else:
                        print(f"🧾 text frame: {m[:240]}")
                return
            finally:
                await ws.close()
        except websockets.exceptions.InvalidStatus as e:
            # 403 from a CloudFront edge, try the next IP
            print(f"🟥 HTTP {getattr(e.response, 'status_code', '???')} from {ip or host}, trying next …")
            last_err = e
            continue
        except Exception as e:
            print(f"❌ Connect/recv error on {ip or host}: {e}")
            last_err = e
            continue

    # If we got here, everything failed
    raise last_err if last_err else RuntimeError("No candidate host/IP could be connected.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--sni", default=MEXC_WSS_HOST)
    ap.add_argument("--override-ip", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    asyncio.run(probe(args.symbol, args.sni, args.override_ip, args.verbose))


if __name__ == "__main__":
    main()
