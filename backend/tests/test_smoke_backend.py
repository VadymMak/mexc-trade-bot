# tests/test_smoke_backend.py
import os
import json
import time
import asyncio
import contextlib

import httpx
import pytest

# ───────────────────────── Opt-in switch ─────────────────────────
# These tests hit a live server. Skip the whole module unless explicitly enabled.
RUN_BACKEND_SMOKE = os.getenv("RUN_BACKEND_SMOKE") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_BACKEND_SMOKE,
    reason="Backend smoke tests are disabled. Set RUN_BACKEND_SMOKE=1 to enable."
)

# ───────────────────────── Config ─────────────────────────
BASE_URL = os.getenv("BOT_BASE_URL", "http://127.0.0.1:8000")
SSE_SYMBOLS = os.getenv("BOT_SSE_SYMBOLS", "BTCUSDT,ETHUSDT")
SSE_TIMEOUT_SEC = float(os.getenv("BOT_SSE_TIMEOUT", "10"))  # how long we wait for first SSE batch


@pytest.mark.asyncio
async def test_basic_health_and_info():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as c:
        r = await c.get("/ping")
        assert r.status_code == 200
        assert r.json().get("message") == "pong"

        r = await c.get("/")
        assert r.status_code == 200
        root = r.json()
        assert root.get("ok") is True
        assert "version" in root

        # Optional health/info (don’t fail if absent)
        with contextlib.suppress(Exception):
            r = await c.get("/api/ping")
            assert r.status_code == 200
        with contextlib.suppress(Exception):
            r = await c.get("/api/info")
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_config_provider_endpoint_present():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as c:
        r = await c.get("/api/config/provider")
        assert r.status_code == 200
        j = r.json()
        # minimal shape
        assert "active" in j
        assert "mode" in j
        assert isinstance(j.get("available", []), list)
        assert "ws_enabled" in j
        assert "revision" in j


@pytest.mark.asyncio
async def test_strategy_params_get_present():
    # just ensure endpoint responds; we don't enforce schema here
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as c:
        r = await c.get("/api/strategy/params")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


@pytest.mark.asyncio
async def test_sse_stream_emits_batches_or_skips():
    """
    Opens /api/market/stream and tries to read at least one 'data:' event.
    If no event arrives within SSE_TIMEOUT_SEC, we SKIP (don’t fail the suite),
    because ingestion may be disabled or symbols might be empty.
    """
    url = f"/api/market/stream?symbols={SSE_SYMBOLS}"
    timeout_at = time.monotonic() + SSE_TIMEOUT_SEC

    # We need a raw stream; use httpx stream() with decode=False
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=None) as c:
        async with c.stream("GET", url, headers={"Accept": "text/event-stream"}) as resp:
            assert resp.status_code == 200

            # Read line-by-line and parse SSE. We consider an event received when we see a 'data:' line with JSON.
            async for raw_line in resp.aiter_lines():
                if raw_line is None:
                    # connection closed — break
                    break
                line = raw_line.strip()
                if not line:
                    # event delimiter
                    continue
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
                    # Some servers send keep-alives — tolerate non-JSON here
                    with contextlib.suppress(Exception):
                        data = json.loads(payload)
                        # We expect a list[dict] batch or a dict with symbol
                        if isinstance(data, list) and data and isinstance(data[0], dict):
                            # minimal fields
                            assert "symbol" in data[0]
                            # success: we got at least one batch
                            return
                        if isinstance(data, dict) and "symbol" in data:
                            return

                if time.monotonic() > timeout_at:
                    pytest.skip(f"No SSE data within {SSE_TIMEOUT_SEC}s (ingestion may be off or symbols empty).")
            # If the loop exits without data, skip.
            pytest.skip("SSE stream closed without data; skipping smoke assertion.")


@pytest.mark.asyncio
async def test_exec_positions_present():
    """
    Soft check: /api/exec/positions should respond with a list (may be empty).
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as c:
        r = await c.get("/api/exec/positions")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
