# tests/test_scanner_endpoints.py
from __future__ import annotations

import math
import pytest
from fastapi.testclient import TestClient

# Import your FastAPI app
# If your main app is created in app/main.py as `app`, import that.
# Otherwise import wherever your FastAPI() instance lives.
from app.main import app

# We'll patch these in tests
from app.api.scanner import scan_gate_quote, scan_mexc_quote  # type: ignore

# Import ScanRow dataclass to create deterministic rows
from app.services.market_scanner import ScanRow


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _row(
    symbol="BTCUSDT",
    exchange="mexc",
    bid=100.0,
    ask=100.1,
    last=100.05,
    base_vol=1000.0,
    quote_vol=100000.0,
    tpm=60.0,
    upm=50000.0,
    med=800.0,
    maker_fee=0.0,
    taker_fee=0.0005,
    zero_fee=True,
):
    spread_abs = ask - bid
    mid = (bid + ask) / 2.0
    spread_bps = (spread_abs / mid) * 1e4
    spread_pct = (spread_abs / mid) * 100.0

    r = ScanRow(
        symbol=symbol,
        exchange=exchange,
        bid=bid,
        ask=ask,
        last=last,
        spread_abs=spread_abs,
        spread_pct=spread_pct,
        spread_bps=spread_bps,
        base_volume_24h=base_vol,
        quote_volume_24h=quote_vol,
        depth_at_bps={
            5: {"bid_usd": 2_500_000.0, "ask_usd": 2_400_000.0},
            10: {"bid_usd": 5_000_000.0, "ask_usd": 5_100_000.0},
            # Any extra level should be allowed by extra="allow"
            3: {"bid_usd": 1_000_000.0, "ask_usd": 950_000.0},
        },
        trades_per_min=tpm,
        usd_per_min=upm,
        median_trade_usd=med,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        zero_fee=zero_fee,
        imbalance=0.26,
        ws_lag_ms=None,
        reason="ok",
        reasons_all=["unit-test"],
    )
    return r


@pytest.fixture(autouse=True)
def patch_scanners(monkeypatch):
    """Patch scan functions to return deterministic rows."""
    async def fake_gate(**kwargs):
        return [
            _row(symbol="ETHUSDT", exchange="gate", bid=2000, ask=2000.3, upm=120000.0, quote_vol=1_000_000.0),
            _row(symbol="BTCUSDT", exchange="gate", bid=120000, ask=120000.2, upm=800000.0, quote_vol=2_000_000.0),
        ]

    async def fake_mexc(**kwargs):
        return [
            _row(symbol="ETHUSDT", exchange="mexc", bid=2001, ask=2001.2, upm=110000.0, quote_vol=900_000.0),
            _row(symbol="BTCUSDT", exchange="mexc", bid=120100, ask=120100.1, upm=700000.0, quote_vol=1_800_000.0),
        ]

    monkeypatch.setattr("app.api.scanner.scan_gate_quote", fake_gate, raising=True)
    monkeypatch.setattr("app.api.scanner.scan_mexc_quote", fake_mexc, raising=True)


def _assert_scan_item_shape(item: dict):
    # Basic required fields
    for k in [
        "exchange","symbol","bid","ask","last",
        "spread_abs","spread_pct","spread_bps",
        "base_volume_24h","quote_volume_24h",
        "trades_per_min","usd_per_min","median_trade_usd",
        "imbalance","maker_fee","taker_fee","zero_fee",
        "depth5_bid_usd","depth5_ask_usd","depth10_bid_usd","depth10_ask_usd",
        # Effective spread aliases
        "eff_spread_bps","eff_spread_pct","eff_spread_abs",
        "eff_spread_bps_taker","eff_spread_pct_taker","eff_spread_abs_taker",
        "eff_spread_bps_maker","eff_spread_pct_maker","eff_spread_abs_maker",
        "score",
    ]:
        assert k in item, f"missing key: {k}"

    # Type sanity
    assert isinstance(item["score"], (int, float))
    assert isinstance(item["eff_spread_bps"], (int, float))
    assert isinstance(item["eff_spread_bps_taker"], (int, float))
    assert isinstance(item["eff_spread_bps_maker"], (int, float))

    # Values are non-negative in our fake rows
    assert item["eff_spread_bps"] >= 0.0
    assert item["depth5_bid_usd"] >= 0.0
    assert item["depth10_ask_usd"] >= 0.0


def test_gate_top(client: TestClient):
    resp = client.get(
        "/api/scanner/gate/top",
        params={"quote": "USDT", "limit": 10, "explain": "true"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list) and len(data) >= 1
    _assert_scan_item_shape(data[0])
    # explain adds reason
    assert "reason" in data[0]


def test_mexc_top(client: TestClient):
    resp = client.get(
        "/api/scanner/mexc/top",
        params={"quote": "USDT", "limit": 10, "explain": "false"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list) and len(data) >= 1
    _assert_scan_item_shape(data[0])
    # reason is optional when explain=false
    # (router may still include it if underlying row has it)
    assert "score" in data[0]


def test_top_all(client: TestClient):
    resp = client.get(
        "/api/scanner/top",
        params={"exchange": "all", "quote": "USDT", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list) and len(data) >= 2
    for item in data:
        _assert_scan_item_shape(item)


def test_openapi_has_new_fields(client: TestClient):
    """Ensure the OpenAPI/Swagger schema advertises new fields."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, resp.text
    spec = resp.json()

    # Find our ScanItem schema (FastAPI names it from the Pydantic model)
    # Components → schemas → ScanItem
    schemas = spec.get("components", {}).get("schemas", {})
    assert "ScanItem" in schemas, "ScanItem schema missing in OpenAPI"
    scan_schema = schemas["ScanItem"]
    props = scan_schema.get("properties", {})

    for k in [
        "score",
        "eff_spread_bps","eff_spread_pct","eff_spread_abs",
        "eff_spread_bps_taker","eff_spread_pct_taker","eff_spread_abs_taker",
        "eff_spread_bps_maker","eff_spread_pct_maker","eff_spread_abs_maker",
        "depth5_bid_usd","depth5_ask_usd","depth10_bid_usd","depth10_ask_usd",
    ]:
        assert k in props, f"{k} not advertised in OpenAPI schema"
