from __future__ import annotations

from typing import List, Tuple, Optional
from time import time
from math import isclose

from app.schemas.scanner import Metrics, FeatureSnapshot, Tier, FeeInfo
from app.scoring.presets import get_preset


class GateFail(Exception):
    """Internal helper to mark a hard exclusion gate."""
    pass


def _now_ms() -> int:
    return int(time() * 1000)


def _is_fallback_candles(m: Metrics, *, atr_sentinel: float, min_atr1m_pct: float) -> bool:
    """
    Detect placeholder candle stats, supporting two patterns:
    1) Fixed legacy sentinel (atr ≈ atr_sentinel, spikes=0, pullback≈0.35, grinder≈0.30)
    2) Router/preset-based placeholder (atr ≈ 0.9 * min_atr1m_pct, spikes=0, pullback≈0.35, grinder≈0.30)
    """
    eps = 1e-9
    legacy = (
        isclose(m.atr1m_pct, atr_sentinel, rel_tol=0.0, abs_tol=1e-12)
        and m.spike_count_90m == 0
        and isclose(m.pullback_median_retrace, 0.35, rel_tol=0.0, abs_tol=1e-12)
        and isclose(m.grinder_ratio, 0.30, rel_tol=0.0, abs_tol=1e-12)
    )
    presetish = (
        m.spike_count_90m == 0
        and isclose(m.pullback_median_retrace, 0.35, rel_tol=0.0, abs_tol=1e-12)
        and abs(m.grinder_ratio - 0.30) <= 1e-12
        and abs(m.atr1m_pct - max(min_atr1m_pct * 0.9, 0.001)) <= max(1e-6, min_atr1m_pct * 0.05 + eps)
    )
    return legacy or presetish


def score_metrics(metrics: Metrics, preset_name: str) -> Tuple[int, List[str], Tier]:
    """
    Return (score 0..100, reasons[], tier).
    Score — accumulates from soft criteria.
    Tier — result of hard & soft rules (A/B/Excluded).
    """
    p = get_preset(preset_name)
    reasons: List[str] = []
    score: float = 0.0
    stale = False

    # Detect fallback candles (supports both legacy & preset-based defaults)
    fallback_candles = _is_fallback_candles(
        metrics,
        atr_sentinel=0.135,
        min_atr1m_pct=p.min_atr1m_pct,
    )
    if fallback_candles:
        reasons.append("candles_missing")

    # ---- Hard gates (instant exclusion) ----
    try:
        # Liquidity / friction
        if metrics.usd_per_min < p.min_usd_per_min:
            raise GateFail(f"usd_per_min_low<{p.min_usd_per_min}")
        if metrics.trades_per_min < p.min_trades_per_min:
            raise GateFail(f"trades_per_min_low<{p.min_trades_per_min}")
        if metrics.effective_spread_bps > p.max_spread_bps:
            raise GateFail(f"spread_high>{p.max_spread_bps}")
        if metrics.slip_bps_clip > p.max_slip_bps:
            raise GateFail(f"slippage_high>{p.max_slip_bps}")

        # Volatility — enforce ATR only if not a placeholder
        if not fallback_candles and metrics.atr1m_pct < p.min_atr1m_pct:
            raise GateFail(f"atr1m_pct_low<{p.min_atr1m_pct}")

        # Grinder: too grindy → exclude
        if metrics.grinder_ratio > p.max_grinder_ratio:
            raise GateFail(f"grinder_ratio_high>{p.max_grinder_ratio}")

        # Order book
        if metrics.depth_usd_5bps < p.min_depth5_usd:
            raise GateFail(f"depth5_low<{p.min_depth5_usd}")

        # Staleness (soft penalty flag)
        if metrics.stale_sec is not None and metrics.stale_sec > p.max_stale_sec:
            stale = True
            reasons.append(f"stale_sec>{p.max_stale_sec}")

    except GateFail as gf:
        reasons.append(str(gf))
        return (0, reasons, "Excluded")

    # ---- Soft scoring ----

    # Liquidity — up to 20
    liq_ratio = max(0.0, min(1.0, metrics.usd_per_min / (p.min_usd_per_min * 5.0)))
    score += 20.0 * liq_ratio
    if liq_ratio >= 0.6:
        reasons.append("liq_good")

    # Frequency — up to 10
    tpm_ratio = max(0.0, min(1.0, metrics.trades_per_min / max(p.min_trades_per_min * 2.0, 1.0)))
    score += 10.0 * tpm_ratio
    if tpm_ratio >= 0.6:
        reasons.append("tpm_good")

    # Spread — up to 10
    spread_ratio = max(0.0, min(1.0, p.max_spread_bps / max(metrics.effective_spread_bps, 1e-9)))
    score += 10.0 * spread_ratio
    if metrics.effective_spread_bps <= p.max_spread_bps * 0.7:
        reasons.append("spread_tight")

    # Slippage — up to 10
    slip_ratio = max(0.0, min(1.0, p.max_slip_bps / max(metrics.slip_bps_clip, 1e-9)))
    score += 10.0 * slip_ratio
    if metrics.slip_bps_clip <= p.max_slip_bps * 0.7:
        reasons.append("slippage_ok")

    # Volatility (ATR%) — up to 15
    atr_ratio = max(0.0, min(1.0, metrics.atr1m_pct / max(p.min_atr1m_pct * 2.0, 1e-9)))
    score += 15.0 * atr_ratio
    if atr_ratio >= 0.8:
        reasons.append("atr_active")

    # Spikes 90m — up to 15
    spike_ratio = max(0.0, min(1.0, metrics.spike_count_90m / max(p.min_spike_count_90m, 1)))
    score += 15.0 * spike_ratio
    if metrics.spike_count_90m >= p.min_spike_count_90m:
        reasons.append("spikes_ok")

    # Pullbacks — up to 10
    pb_ratio = max(0.0, min(1.0, metrics.pullback_median_retrace / 0.6))
    score += 10.0 * pb_ratio
    if metrics.pullback_median_retrace >= 0.35:
        reasons.append("pullbacks_ok")

    # Grinder penalty — up to -10
    grind_over = max(0.0, metrics.grinder_ratio - p.target_grinder_ratio)
    score -= min(10.0, grind_over * 30.0)
    if metrics.grinder_ratio <= p.target_grinder_ratio:
        reasons.append("not_grindy")

    # OB imbalances — up to 10
    if p.min_imbalance_hits_60m > 0:
        imb_ratio = max(0.0, min(1.0, metrics.imbalance_sigma_hits_60m / max(p.min_imbalance_hits_60m, 1)))
        score += 10.0 * imb_ratio
        if metrics.imbalance_sigma_hits_60m >= p.min_imbalance_hits_60m:
            reasons.append("ob_imbalances_active")

    # Depth@5bps — up to 10
    depth_ratio = max(0.0, min(1.0, metrics.depth_usd_5bps / max(p.min_depth5_usd * 3.0, 1.0)))
    score += 10.0 * depth_ratio
    if depth_ratio >= 0.5:
        reasons.append("depth_ok")

    # Staleness / fallback penalty
    if stale or fallback_candles:
        score *= 0.8
        reasons.append("score_penalty_stale")

    score_i = int(max(0, min(100, round(score))))

    # ---- Tiering ----
    if score_i >= p.tier_a_min_score:
        tier: Tier = "A"
        reasons.append("tier=A")
    elif score_i >= p.tier_b_min_score:
        tier = "B"
        reasons.append("tier=B")
    else:
        tier = "Excluded"
        reasons.append("tier=Excluded_low_score")

    return score_i, reasons, tier


def snapshot_from_metrics(
    venue: str,
    symbol: str,
    preset_name: str,
    metrics: Metrics,
    fees: Optional[FeeInfo] = None,
) -> FeatureSnapshot:
    score, reasons, tier = score_metrics(metrics, preset_name)
    return FeatureSnapshot(
        ts=_now_ms(),
        venue=venue,
        symbol=symbol,
        preset=preset_name,
        metrics=metrics,
        score=score,
        tier=tier,
        reasons=reasons,
        stale=("score_penalty_stale" in reasons),
        fees=(fees or FeeInfo()),  # ensure schema-valid FeeInfo
    )
