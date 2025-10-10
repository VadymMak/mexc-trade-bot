# scripts/schemas_smoke.py
import json
import os
import sys
from typing import Any, Dict

# Ensure project root is importable when running from /scripts
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schemas.scanner import (  # type: ignore
    ScannerRow,
    Metrics,
    FeatureSnapshot,
    ScannerTopResponse,
    FeeInfo,
)

def _filter_kwargs(model_cls, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep only keys actually defined on the Pydantic model.
    This makes the smoke test tolerant to schema field changes.
    """
    try:
        fields = set(getattr(model_cls, "model_fields").keys())  # Pydantic v2
    except Exception:
        # Fallback for older Pydantic (v1) – won't be needed if you're on v2
        fields = set(getattr(model_cls, "__fields__", {}).keys())
    return {k: v for k, v in data.items() if k in fields}

def test_scanner_row():
    raw = dict(
        exchange="gate",
        symbol="ETHUSDT",
        bid=100.0, ask=100.2, last=100.15,
        spread_abs=0.2, spread_pct=0.1997, spread_bps=19.97,
        base_volume_24h=1_000_000, quote_volume_24h=100_000,
        trades_per_min=12.3, usd_per_min=5200.0, median_trade_usd=420.0,
        maker_fee=0.0002, taker_fee=0.0007, zero_fee=False,
        imbalance=0.55, ws_lag_ms=15,
        # If your schema flattens depth@bps:
        depth5_bid_usd=6000.0, depth5_ask_usd=5800.0,
        depth10_bid_usd=16000.0, depth10_ask_usd=15000.0,
        # If your schema exposes effective spreads (taker/maker):
        eff_spread_bps=21.0, eff_spread_pct=0.21, eff_spread_abs=0.21,
        eff_spread_bps_taker=21.0, eff_spread_pct_taker=0.21, eff_spread_abs_taker=0.21,
        eff_spread_bps_maker=18.0, eff_spread_pct_maker=0.18, eff_spread_abs_maker=0.18,
        score=77.2,
        reason="ok",
        reasons_all=["liq_ok", "spread_tight"],
    )
    row = ScannerRow(**_filter_kwargs(ScannerRow, raw))
    payload = row.model_dump()
    # Light sanity checks so this works in pytest as a “real” test.
    assert payload.get("exchange") in {"gate", "mexc"}
    assert payload.get("symbol")
    assert isinstance(payload.get("quote_volume_24h", 0), (int, float))
    print(json.dumps(payload, indent=2))

def test_tiered_snapshot():
    metrics_raw = dict(
        usd_per_min=8000.0,
        trades_per_min=8.0,
        effective_spread_bps=6.0,
        slip_bps_clip=5.0,
        atr1m_pct=0.15,
        spike_count_90m=4,
        pullback_median_retrace=0.45,
        grinder_ratio=0.25,
        depth_usd_5bps=7000.0,
        imbalance_sigma_hits_60m=1,
        ws_lag_ms=20,
        stale_sec=3,
    )
    metrics = Metrics(**_filter_kwargs(Metrics, metrics_raw))

    snap_raw = dict(
        ts=1_700_000_000_000,
        venue="mexc",
        symbol="DOGEUSDT",
        preset="balanced",
        metrics=metrics,
        score=72,
        tier="excluded",  # lower-case on purpose; schema may normalize
        reasons=["example_flag"],
        stale=False,
        fees=FeeInfo(**_filter_kwargs(FeeInfo, dict(maker=0.0002, taker=0.0006, zero_maker=False))),
    )
    snap = FeatureSnapshot(**_filter_kwargs(FeatureSnapshot, snap_raw))

    # Top response with one snapshot in tierA to exercise nesting
    resp_raw = dict(ts=1_700_000_000_001, preset="balanced", tierA=[snap], tierB=[], excluded=[])
    resp = ScannerTopResponse(**_filter_kwargs(ScannerTopResponse, resp_raw))
    out = resp.model_dump()

    # Light sanity checks (won’t fail if schema renames optional fields)
    assert out.get("preset") == "balanced"
    assert isinstance(out.get("ts"), int)
    assert isinstance(out.get("tierA", []), list)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    test_scanner_row()
    test_tiered_snapshot()
    print("✅ schemas_smoke OK")
