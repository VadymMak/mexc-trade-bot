# Тихий прогон:
# LOG_LEVEL=INFO GATE_WS_LOG_LEVEL=INFO python scripts/test_gate_ws.py

# Детальный только наш клиент (websockets молчит):
# LOG_LEVEL=INFO GATE_WS_LOG_LEVEL=DEBUG python scripts/test_gate_ws.py

# Переключение на LIVE:
# GATE_WS_ENV=LIVE LOG_LEVEL=INFO python scripts/test_gate_ws.py



# scripts/test_gate_ws.py
import asyncio
import contextlib
import logging
import importlib

import os
import sys

# Ensure project root is importable when running from /scripts
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # make websockets quiet
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.protocol").setLevel(logging.WARNING)

    # our client logger level (can override via GATE_WS_LOG_LEVEL)
    gate_logger_level = getattr(logging, os.getenv("GATE_WS_LOG_LEVEL", level_name).upper(), level)
    logging.getLogger("app.market_data.gate_ws").setLevel(gate_logger_level)


async def main() -> None:
    _setup_logging()

    # ---------- FORCE env BEFORE importing settings/client ----------
    # If caller set GATE_WS_ENV outside, honor it; else force TESTNET by default.
    os.environ["ACTIVE_PROVIDER"] = os.getenv("ACTIVE_PROVIDER", "GATE")
    os.environ["GATE_WS_ENV"] = os.getenv("GATE_WS_ENV", "TESTNET")  # LIVE | TESTNET
    os.environ["ENABLE_WS"] = os.getenv("ENABLE_WS", "true")

    # Kill any hard override that can mask per-exchange logic
    os.environ.pop("WS_BASE_URL_RESOLVED", None)

    # Now (re)load settings fresh with the env above
    from app.config import settings as settings_module
    importlib.reload(settings_module)
    settings = settings_module.Settings()  # fresh instance with updated env

    # Reload client modules AFTER settings are fresh
    from app.market_data import gate_ws as gate_ws_module
    importlib.reload(gate_ws_module)
    from app.market_data import ws_client as ws_client_module
    importlib.reload(ws_client_module)
    GateWebSocketClient = ws_client_module.GateWebSocketClient

    # ---- test parameters (env-overridable) ----
    symbols_env = os.getenv("GATE_WS_SYMBOLS", "BTC_USDT")
    symbols = [s.strip().upper() for s in symbols_env.split(",") if s.strip()]

    duration_sec = int(os.getenv("GATE_WS_TEST_SECONDS", "120"))
    depth_limit = int(os.getenv("GATE_WS_DEPTH_LIMIT", "10"))
    ping_interval = float(os.getenv("GATE_WS_PING_INTERVAL", "20.0"))
    ping_timeout = float(os.getenv("GATE_WS_PING_TIMEOUT", "10.0"))

    # ---- print resolved context ----
    ws_url = settings.ws_base_url_resolved
    rest_url = settings.rest_base_url_resolved
    gate_env = (settings.gate_ws_env or "").strip().upper() or "(fallback to ACTIVE_MODE)"
    provider = settings.active_provider
    mode = settings.active_mode

    print("=== Gate WS Test ===")
    print(f"Provider         : {provider}")
    print(f"Global Mode      : {mode}")
    print(f"GATE_WS_ENV      : {gate_env}")
    print(f"Resolved WS URL  : {ws_url}")
    print(f"Resolved REST URL: {rest_url}")
    print(f"Symbols          : {symbols}")
    print(f"Duration (sec)   : {duration_sec}")
    print(f"Depth limit      : {depth_limit}")
    print("================================================================")

    # ---- run client ----
    client = GateWebSocketClient(
        symbols,
        depth_limit=depth_limit,
        want_tickers=True,
        want_order_book=True,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
    )

    task = asyncio.create_task(client.run())
    try:
        await asyncio.sleep(duration_sec)
    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        with contextlib.suppress(Exception):
            await client.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    print("Test ended.")


if __name__ == "__main__":
    if os.name == "nt":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    asyncio.run(main())
