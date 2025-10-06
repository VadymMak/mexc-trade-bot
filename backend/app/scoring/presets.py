from __future__ import annotations

from typing import TypedDict, Dict, Iterable


class Preset(TypedDict):
    # Liquidity / friction
    min_usd_per_min: float         # minimum traded USD per minute
    min_trades_per_min: int        # minimum trades per minute
    max_spread_bps: float          # (ask-bid)/mid must be <= this, in bps
    max_slip_bps: float            # simulated clip slippage must be <= this, in bps

    # Volatility / shape
    min_atr1m_pct: float           # ATR(20) on 1m / last close must be >=
    min_spike_count_90m: int       # spike candles required in the last 90m (soft-scored)
    max_grinder_ratio: float       # hard gate: > -> Excluded
    target_grinder_ratio: float    # soft target: above it -> score penalty

    # Order book / tape
    min_depth5_usd: float          # depth within ±5 bps must be >=
    min_depth10_usd: float         # depth within ±10 bps must be >=
    min_imbalance_hits_60m: int    # OB imbalance spikes in last 60m (0 to ignore)

    # Data quality
    max_stale_sec: int             # if metrics.stale_sec > this -> stale penalty (not hard exclude)

    # Tiering thresholds
    tier_a_min_score: int
    tier_b_min_score: int


# ---------- Presets ----------
CONSERVATIVE: Preset = {
    "min_usd_per_min": 5_000.0,
    "min_trades_per_min": 5,
    "max_spread_bps": 6.0,
    "max_slip_bps": 6.0,

    "min_atr1m_pct": 0.20,
    "min_spike_count_90m": 6,
    "max_grinder_ratio": 0.60,
    "target_grinder_ratio": 0.40,

    "min_depth5_usd": 10_000.0,
    "min_depth10_usd": 15_000.0,
    "min_imbalance_hits_60m": 2,

    "max_stale_sec": 10,

    "tier_a_min_score": 70,
    "tier_b_min_score": 45,
}

BALANCED: Preset = {
    "min_usd_per_min": 3_000.0,
    "min_trades_per_min": 3,
    "max_spread_bps": 8.0,
    "max_slip_bps": 8.0,

    "min_atr1m_pct": 0.15,
    "min_spike_count_90m": 5,
    "max_grinder_ratio": 0.65,
    "target_grinder_ratio": 0.45,

    "min_depth5_usd": 5_000.0,
    "min_depth10_usd": 7_500.0,
    "min_imbalance_hits_60m": 1,

    "max_stale_sec": 15,

    "tier_a_min_score": 65,
    "tier_b_min_score": 40,
}

AGGRESSIVE: Preset = {
    "min_usd_per_min": 1_000.0,
    "min_trades_per_min": 1,
    "max_spread_bps": 10.0,
    "max_slip_bps": 10.0,

    "min_atr1m_pct": 0.10,
    "min_spike_count_90m": 3,
    "max_grinder_ratio": 0.70,
    "target_grinder_ratio": 0.50,

    "min_depth5_usd": 2_000.0,
    "min_depth10_usd": 3_000.0,
    "min_imbalance_hits_60m": 0,

    "max_stale_sec": 20,

    "tier_a_min_score": 60,
    "tier_b_min_score": 35,
}

_PRESETS: Dict[str, Preset] = {
    "conservative": CONSERVATIVE,
    "balanced": BALANCED,
    "aggressive": AGGRESSIVE,
}

# convenient aliases → canonical keys
_ALIASES: Dict[str, str] = {
    "default": "balanced",
    "balance": "balanced",
    "std": "balanced",
    "conservative": "conservative",
    "aggressive": "aggressive",
}


class PresetObj(dict):
    """
    Dict that also allows attribute-style access: obj.key
    (We return a shallow copy per request to avoid accidental mutation.)
    """
    __getattr__ = dict.get

    def keys(self) -> Iterable[str]:  # type: ignore[override]
        return super().keys()


def available_presets() -> Dict[str, Preset]:
    """Return a read-only mapping of canonical preset names to definitions."""
    # return a shallow copy to prevent mutation
    return dict(_PRESETS)


def get_preset(name: str) -> PresetObj:
    """
    Return a preset as a dict-like object with attribute access.
    Allowed names: conservative | balanced | aggressive
    Aliases: default | balance | std -> balanced
    """
    key = (name or "").lower().strip()
    key = _ALIASES.get(key, key)
    try:
        return PresetObj(dict(_PRESETS[key]))  # shallow copy
    except KeyError:
        allowed = ", ".join(sorted(_PRESETS.keys()))
        raise ValueError(f"Unknown preset '{name}'. Allowed: {allowed}")


# Back-compat export for existing imports in market_scanner, etc.
PRESETS = _PRESETS

__all__ = [
    "Preset",
    "PresetObj",
    "get_preset",
    "available_presets",
    "PRESETS",
    "CONSERVATIVE",
    "BALANCED",
    "AGGRESSIVE",
]
