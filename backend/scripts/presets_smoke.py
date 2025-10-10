import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring.presets import get_preset, available_presets

def check(name: str):
    p = get_preset(name)
    required = [
        "min_usd_per_min","min_trades_per_min","max_spread_bps","max_slip_bps",
        "min_atr1m_pct","min_spike_count_90m","max_grinder_ratio","target_grinder_ratio",
        "min_depth5_usd","min_depth10_usd","min_imbalance_hits_60m",
        "max_stale_sec","tier_a_min_score","tier_b_min_score"
    ]
    missing = [k for k in required if k not in p]
    assert not missing, f"{name}: missing keys {missing}"
    assert p["tier_a_min_score"] >= p["tier_b_min_score"], f"{name}: tier A must be ≥ tier B"
    return { "name": name, **p }

if __name__ == "__main__":
    results = [check(n) for n in sorted(available_presets().keys())]
    print(json.dumps(results, indent=2))
