from __future__ import annotations

from typing import TypedDict, Dict, Iterable, List, Optional


class Preset(TypedDict, total=False):
    # ---- Liquidity / friction (yours) ----
    min_usd_per_min: float         # minimum traded USD per minute
    min_trades_per_min: int        # minimum trades per minute
    max_spread_bps: float          # (ask-bid)/mid must be <= this, in bps
    max_slip_bps: float            # simulated clip slippage must be <= this, in bps

    # ---- Volatility / shape (yours) ----
    # IMPORTANT: atr1m_pct is a FRACTION (e.g., 0.0015 = 0.15%), not percent.
    min_atr1m_pct: float           # ATR(20) on 1m / last close must be >= (fraction)
    min_spike_count_90m: int       # spike candles required in the last 90m (soft-scored)
    max_grinder_ratio: float       # hard gate: > -> Excluded
    target_grinder_ratio: float    # soft target: above it -> score penalty

    # ---- Order book / tape (yours) ----
    min_depth5_usd: float          # depth within ±5 bps must be >=
    min_depth10_usd: float         # depth within ±10 bps must be >=
    min_imbalance_hits_60m: int    # OB imbalance spikes in last 60m (0 to ignore)

    # ---- Data quality (yours) ----
    max_stale_sec: int             # if metrics.stale_sec > this -> stale penalty (not hard exclude)

    # ---- Tiering thresholds (yours) ----
    tier_a_min_score: int
    tier_b_min_score: int

    # ---- Scanner kwargs (consumed by scan_gate_quote / scan_mexc_quote) ----
    min_quote_vol_usd: float
    min_spread_pct: float          # optional; set to 0.0 if unused to avoid KeyError
    depth_levels_bps: List[int]
    min_median_trade_usd: float
    min_vol_pattern: float
    max_atr_proxy: float
    activity_ratio: float
    liquidity_test: bool
    symbols: Optional[List[str]]


# ---------- Presets ----------
CONSERVATIVE: Preset = {
    # liquidity / friction
    "min_usd_per_min": 5_000.0,
    "min_trades_per_min": 5,
    "max_spread_bps": 6.0,
    "max_slip_bps": 6.0,

    # volatility / shape (FRACTIONS, not percent)
    "min_atr1m_pct": 0.0020,  # 0.20%
    "min_spike_count_90m": 6,
    "max_grinder_ratio": 0.60,
    "target_grinder_ratio": 0.40,

    # order book / tape
    "min_depth5_usd": 10_000.0,
    "min_depth10_usd": 15_000.0,
    "min_imbalance_hits_60m": 2,

    # data quality
    "max_stale_sec": 10,

    # tiering
    "tier_a_min_score": 70,
    "tier_b_min_score": 45,

    # ---- scanner kwargs (used) ----
    "min_quote_vol_usd": 100_000.0,
    "min_spread_pct": 0.0,        # keep 0.0 if not gating by spread%
    "depth_levels_bps": [5, 10],
    "min_median_trade_usd": 5.0,
    "min_vol_pattern": 60.0,
    "max_atr_proxy": 5.0,
    "activity_ratio": 0.15,
    "liquidity_test": True,
    "symbols": None,
}

BALANCED: Preset = {
    "min_usd_per_min": 3_000.0,
    "min_trades_per_min": 5,            # ← БЫЛО: 3, СТАЛО: 5 (больше активности)
    "max_spread_bps": 8.0,
    "max_slip_bps": 8.0,

    # volatility / shape (FRACTIONS)
    "min_atr1m_pct": 0.0020,            # ← БЫЛО: 0.0015, СТАЛО: 0.0020 (больше движения)
    "min_spike_count_90m": 5,
    "max_grinder_ratio": 0.50,          # ← БЫЛО: 0.65, СТАЛО: 0.50 (меньше chop)
    "target_grinder_ratio": 0.35,       # ← БЫЛО: 0.45, СТАЛО: 0.35 (меньше chop)

    "min_depth5_usd": 5_000.0,
    "min_depth10_usd": 7_500.0,
    "min_imbalance_hits_60m": 1,

    "max_stale_sec": 15,

    "tier_a_min_score": 65,
    "tier_b_min_score": 40,

    # scanner kwargs
    "min_quote_vol_usd": 50_000.0,
    "min_spread_pct": 0.0,
    "depth_levels_bps": [5, 10],
    "min_median_trade_usd": 5.0,        # ← БЫЛО: 0.0, СТАЛО: 5.0 (фильтр мелких сделок)
    "min_vol_pattern": 70.0,            # ← БЫЛО: 0.0, СТАЛО: 70.0 (стабильность!)
    "max_atr_proxy": 8.0,               # ← БЫЛО: inf, СТАЛО: 8.0 (hedgehog лимит!)
    "activity_ratio": 0.15,             # ← БЫЛО: 0.10, СТАЛО: 0.15 (больше активности)
    "liquidity_test": True,             # ← БЫЛО: False, СТАЛО: True (проверка ликвидности!)
    "symbols": None,
}

AGGRESSIVE: Preset = {
    "min_usd_per_min": 1_000.0,
    "min_trades_per_min": 1,
    "max_spread_bps": 10.0,
    "max_slip_bps": 10.0,

    # FRACTIONS
    "min_atr1m_pct": 0.0010,  # 0.10%
    "min_spike_count_90m": 3,
    "max_grinder_ratio": 0.70,
    "target_grinder_ratio": 0.50,

    "min_depth5_usd": 2_000.0,
    "min_depth10_usd": 3_000.0,
    "min_imbalance_hits_60m": 0,

    "max_stale_sec": 20,

    "tier_a_min_score": 60,
    "tier_b_min_score": 35,

    # scanner kwargs
    "min_quote_vol_usd": 10_000.0,
    "min_spread_pct": 0.0,
    "depth_levels_bps": [5, 10],
    "min_median_trade_usd": 0.0,
    "min_vol_pattern": 0.0,
    "max_atr_proxy": float("inf"),
    "activity_ratio": 0.0,
    "liquidity_test": False,
    "symbols": None,
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
