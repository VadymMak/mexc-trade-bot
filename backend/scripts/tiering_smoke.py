# scripts/tiering_smoke.py
from __future__ import annotations
import json, os, sys

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring.tiering import score_metrics
from app.scoring.presets import get_preset
from app.schemas.scanner import Metrics  # pydantic model or TypedDict-like

def mk(**kw) -> Metrics:
    # sensible defaults that pass "balanced" unless overridden
    base = dict(
        usd_per_min=6000.0,
        trades_per_min=8.0,
        effective_spread_bps=3.0,
        slip_bps_clip=3.0,
        atr1m_pct=0.18,
        spike_count_90m=7,
        pullback_median_retrace=0.45,
        grinder_ratio=0.30,
        depth_usd_5bps=8000.0,
        imbalance_sigma_hits_60m=2,
        ws_lag_ms=None,
        stale_sec=None,
    )
    base.update(kw)
    return Metrics(**base)

cases = {
    "balanced_ok": mk(),
    "low_liq": mk(usd_per_min=1000.0),           # hard-exclude
    "spread_high": mk(effective_spread_bps=20),  # hard-exclude
    "grindy": mk(grinder_ratio=0.75),            # hard-exclude
    "fallback_candles": mk(                      # should not ATR-fail; gets penalty
        atr1m_pct=round(get_preset("balanced").min_atr1m_pct * 0.9, 6),
        spike_count_90m=0,
        pullback_median_retrace=0.35,
        grinder_ratio=0.30,
    ),
}

def run_one(name: str, preset: str):
    s, reasons, tier = score_metrics(cases[name], preset)
    return dict(name=name, preset=preset, score=s, tier=tier, reasons=reasons)

if __name__ == "__main__":
    rows = []
    for preset in ("conservative", "balanced", "aggressive"):
        rows.append(run_one("balanced_ok", preset))
        rows.append(run_one("fallback_candles", preset))
        rows.append(run_one("low_liq", preset))
        rows.append(run_one("spread_high", preset))
        rows.append(run_one("grindy", preset))

    print(json.dumps(rows, indent=2))

    # Simple assertions (raise if broken)
    ok_bal = [r for r in rows if r["name"]=="balanced_ok" and r["preset"]=="balanced"][0]
    assert ok_bal["tier"] in ("A","B"), f"expected A/B for balanced_ok, got {ok_bal}"

    low_liq_bal = [r for r in rows if r["name"]=="low_liq" and r["preset"]=="balanced"][0]
    assert low_liq_bal["tier"] == "Excluded", f"low_liq should exclude, got {low_liq_bal}"

    spread_high_bal = [r for r in rows if r["name"]=="spread_high" and r["preset"]=="balanced"][0]
    assert spread_high_bal["tier"] == "Excluded", f"spread_high should exclude, got {spread_high_bal}"

    fallback_bal = [r for r in rows if r["name"]=="fallback_candles" and r["preset"]=="balanced"][0]
    assert "candles_missing" in fallback_bal["reasons"], "expected candles_missing flag"
    # should still be tiered (A/B/Excluded depending on penalty+other metrics)
    assert fallback_bal["tier"] in ("A","B","Excluded")
    print("\n✅ tiering_smoke: assertions passed")
