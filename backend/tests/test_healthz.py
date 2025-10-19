import pytest
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_healthz_reports_cache_hitrate(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Try to warm candles cache (if scanner endpoint is enabled in this build)
        params = {"quote": "USDT", "limit": 1, "fetch_candles": True}
        w1 = await ac.get("/api/scanner/mexc/top", params=params)
        w2 = await ac.get("/api/scanner/mexc/top", params=params)
        warmed = (w1.status_code == 200 and w2.status_code == 200)

        # health can return 200 (no warnings) or 503 (warnings) — both OK for this test
        r = await ac.get("/api/healthz")
        assert r.status_code in (200, 503)
        data = r.json()

        # Find hitrate in either flat or nested schema
        hitrate = None
        if "cache_hitrate" in data:
            hitrate = data["cache_hitrate"]
        elif isinstance(data.get("cache"), dict):
            hitrate = (
                data["cache"].get("candles_hitrate")  # preferred key from your payload
                if "candles_hitrate" in data["cache"]
                else data["cache"].get("hitrate")
            )

        assert hitrate is not None, "cache hit-rate not found in /healthz payload"
        assert isinstance(hitrate, (int, float))
        assert 0.0 <= float(hitrate) <= 1.0

        # Only enforce > 0 when warm-up actually ran (and returns were 200).
        # In CI/offline, warm-up may not fetch candles, so 0.0 is acceptable.
        if warmed:
            assert hitrate > 0.0
