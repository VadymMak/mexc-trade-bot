# scripts/test_mexc_ws.py
import asyncio
import sys
import argparse
import signal
import contextlib
from pathlib import Path
from typing import List

# --- Make project root importable when running as a file ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# -----------------------------------------------------------

from app.config.settings import settings
from app.market_data.ws_client import MEXCWebSocketClient

def _parse_csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]

def _normalize_channels(chs: List[str]) -> List[str]:
    """
    Return BASE topics only; the client will append:
      - bookTicker/deals/aggre.depth: @{rate_suffix}@{SYMBOL}
      - limit.depth: @{SYMBOL}@{LEVELS}
    Also strip any '@snapshot' (MEXC blocks it).
    """
    out: List[str] = []
    for ch in chs:
        raw = ch.strip()
        key = raw.upper()
        if key == "BOOK_TICKER":
            out.append("spot@public.aggre.bookTicker.v3.api.pb")
        elif key == "DEALS":
            out.append("spot@public.aggre.deals.v3.api.pb")
        elif key == "DEPTH_LIMIT":
            out.append("spot@public.limit.depth.v3.api.pb")
        else:
            c = raw
            if c.lower().endswith("@snapshot"):
                c = c.rsplit("@snapshot", 1)[0]
            out.append(c)
    return out

async def _run(symbols: List[str], channels: List[str], minutes: float) -> None:
    # Echo environment resolution
    print(f"⚙️  ACTIVE_PROVIDER: {settings.active_provider}")
    print(f"⚙️  WS_BASE_URL_RESOLVED (property): {settings.ws_base_url_resolved}")
    print(f"⚙️  REST_BASE_URL_RESOLVED (property): {settings.rest_base_url_resolved}")
    print(f"⚙️  Settings symbols: {settings.symbols}")

    norm_channels = _normalize_channels(channels)
    client = MEXCWebSocketClient(symbols, channels=norm_channels)
    print(f"WS URL (client): {client.ws_url}")
    print(f"Channels (normalized): {norm_channels}  Symbols: {symbols}")
    print(f"Starting MEXC WS test for {minutes:.1f} min…")

    task = asyncio.create_task(client.run())

    # Graceful shutdown on Ctrl-C / SIGTERM
    stop_event = asyncio.Event()
    def _on_stop(): stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_stop)
        except NotImplementedError:
            pass

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=minutes * 60.0)
    except asyncio.TimeoutError:
        pass
    finally:
        await client.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        print("Test ended.")

def main():
    p = argparse.ArgumentParser(description="MEXC WebSocket probe")
    p.add_argument("--symbols", default="BTCUSDT", help="Comma-separated symbols (e.g. BTCUSDT,PLBUSDT)")
    p.add_argument("--channels", default="DEPTH_LIMIT", help="Comma-separated channel KEYS or full topics")
    p.add_argument("--minutes", type=float, default=5.0, help="Duration to run")
    args = p.parse_args()

    symbols  = _parse_csv(args.symbols)
    channels = _parse_csv(args.channels)

    asyncio.run(_run(symbols, channels, args.minutes))

if __name__ == "__main__":
    main()
