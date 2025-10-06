from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator

# Accept both lowercase and capitalized to be router/tiering-proof
Tier = Literal["A", "B", "Excluded", "excluded"]


# ─────────────────────────── RAW row for /scanner/*/top ───────────────────────────

class ScannerRow(BaseModel):
    """
    Raw scanner row returned by /api/scanner/gate/top, /api/scanner/mexc/top, and /api/scanner/top.
    Includes effective spread fields and composite score for Swagger to expose in the schema.
    """
    # Identity
    exchange: str = Field(..., description="Exchange/venue identifier, e.g. 'gate' or 'mexc'")
    symbol: str = Field(..., description="Trading symbol, e.g. 'ETHUSDT'")

    # Top-of-book
    bid: float = Field(..., description="Best bid")
    ask: float = Field(..., description="Best ask")
    last: float = Field(..., description="Last traded price")

    # Raw spread
    spread_abs: float = Field(..., description="ask - bid (absolute)")
    spread_pct: float = Field(..., description="(ask - bid) / mid * 100")
    spread_bps: float = Field(..., description="(ask - bid) / mid * 1e4")

    # 24h stats
    base_volume_24h: float = Field(..., description="24h base asset volume")
    quote_volume_24h: float = Field(..., description="24h quote asset volume in USD terms when applicable")

    # Tape (≈60s)
    trades_per_min: float = Field(..., description="Trades per minute (recent window)")
    usd_per_min: float = Field(..., description="Notional turnover per minute in USD (recent window)")
    median_trade_usd: float = Field(..., description="Median trade size in USD (recent window)")

    # Fees
    maker_fee: Optional[float] = Field(None, description="Maker fee (fraction, e.g. 0.0005 for 0.05%)")
    taker_fee: Optional[float] = Field(None, description="Taker fee (fraction)")
    zero_fee: Optional[bool] = Field(None, description="True if maker fee is exactly 0.0 for this venue/symbol")

    # OB shape
    imbalance: Optional[float] = Field(None, description="Top-of-book size imbalance (0..1), 0.5 if unknown")
    ws_lag_ms: Optional[int] = Field(None, description="Estimated WS lag for this symbol, if available")

    # Depth (±bps windows)
    depth5_bid_usd: Optional[float] = Field(None, description="USD depth on bid side within ±5 bps from mid")
    depth5_ask_usd: Optional[float] = Field(None, description="USD depth on ask side within ±5 bps from mid")
    depth10_bid_usd: Optional[float] = Field(None, description="USD depth on bid side within ±10 bps from mid")
    depth10_ask_usd: Optional[float] = Field(None, description="USD depth on ask side within ±10 bps from mid")

    # Effective spread (single-leg) — default aliases point to *taker*
    eff_spread_bps: Optional[float] = Field(None, description="Effective spread in bps (alias of taker variant)")
    eff_spread_pct: Optional[float] = Field(None, description="Effective spread in percent (alias of taker variant)")
    eff_spread_abs: Optional[float] = Field(None, description="Effective spread in absolute price units (alias taker)")

    eff_spread_bps_taker: Optional[float] = Field(None, description="Effective spread in bps for taker")
    eff_spread_pct_taker: Optional[float] = Field(None, description="Effective spread in percent for taker")
    eff_spread_abs_taker: Optional[float] = Field(None, description="Effective spread in absolute units for taker")

    eff_spread_bps_maker: Optional[float] = Field(None, description="Effective spread in bps for maker")
    eff_spread_pct_maker: Optional[float] = Field(None, description="Effective spread in percent for maker")
    eff_spread_abs_maker: Optional[float] = Field(None, description="Effective spread in absolute units for maker")

    # Composite score
    score: Optional[float] = Field(None, description="Composite heuristic score (higher is better)")

    # Diagnostics / explain
    reason: Optional[str] = Field(None, description="Short classification/explain note")
    reasons_all: Optional[List[str]] = Field(
        None,
        description="Full list of matched reasons/flags/fallbacks when explain=true (e.g. ['fallback:tape_5m']).",
    )

    # Важно: позволяем игнорировать неизвестные поля в будущем (стабильность контракта)
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "exchange": "gate",
                    "symbol": "ETHUSDT",
                    "bid": 4473.76,
                    "ask": 4474.82,
                    "last": 4474.38,
                    "spread_abs": 1.06,
                    "spread_pct": 0.0237,
                    "spread_bps": 2.3691,
                    "base_volume_24h": 593.1495,
                    "quote_volume_24h": 2642944.768,
                    "trades_per_min": 1.6,
                    "usd_per_min": 1555.79,
                    "median_trade_usd": 1450.0,
                    "maker_fee": 0.0002,
                    "taker_fee": 0.0006,
                    "zero_fee": False,
                    "imbalance": 0.58,
                    "ws_lag_ms": 12,
                    "depth5_bid_usd": 5980.23,
                    "depth5_ask_usd": 5668.93,
                    "depth10_bid_usd": 16326.15,
                    "depth10_ask_usd": 16616.41,
                    "eff_spread_bps": 2.9691,
                    "eff_spread_pct": 0.029691,
                    "eff_spread_abs": 1.33,
                    "eff_spread_bps_taker": 2.9691,
                    "eff_spread_pct_taker": 0.029691,
                    "eff_spread_abs_taker": 1.33,
                    "eff_spread_bps_maker": 2.5691,
                    "eff_spread_pct_maker": 0.025691,
                    "eff_spread_abs_maker": 1.15,
                    "score": 11.9,
                    "reason": None,
                    "reasons_all": None
                },
                {
                    "exchange": "mexc",
                    "symbol": "AI16ZUSDT",
                    "bid": 0.09619,
                    "ask": 0.09621,
                    "last": 0.09622,
                    "spread_abs": 0.00002,
                    "spread_pct": 0.02079,
                    "spread_bps": 2.0790,
                    "base_volume_24h": 107405670.02,
                    "quote_volume_24h": 10427382.23,
                    "trades_per_min": 4.0,
                    "usd_per_min": 3659.54,
                    "median_trade_usd": 1633.17,
                    "maker_fee": 0.0001,
                    "taker_fee": 0.0005,
                    "zero_fee": False,
                    "imbalance": 0.44,
                    "ws_lag_ms": 8,
                    "depth5_bid_usd": 6358.23,
                    "depth5_ask_usd": 7924.45,
                    "depth10_bid_usd": 11781.28,
                    "depth10_ask_usd": 15180.25,
                    "eff_spread_bps": 2.5790,
                    "eff_spread_pct": 0.02579,
                    "eff_spread_abs": 0.000025,
                    "eff_spread_bps_taker": 2.5790,
                    "eff_spread_pct_taker": 0.02579,
                    "eff_spread_abs_taker": 0.000025,
                    "eff_spread_bps_maker": 2.1790,
                    "eff_spread_pct_maker": 0.02179,
                    "eff_spread_abs_maker": 0.000021,
                    "score": 12.73,
                    "reason": "ok",
                    "reasons_all": ["fallback:tape_5m"]
                }
            ]
        },
    )


# ─────────────────────────── Tiered snapshot models ───────────────────────────

class Metrics(BaseModel):
    # Liquidity / friction
    usd_per_min: float = Field(..., description="Notional turnover per minute in USD")
    trades_per_min: float = Field(..., description="Trades per minute (rate)")
    effective_spread_bps: float = Field(..., description="(ask-bid)/mid in bps")
    slip_bps_clip: float = Field(..., description="Simulated slippage (bps) for configured clip size")

    # Volatility / shape
    atr1m_pct: float = Field(..., description="ATR(20) on 1m bars divided by last close")
    spike_count_90m: int = Field(..., description="Count of spike candles in last 90 minutes")
    pullback_median_retrace: float = Field(..., description="Median retrace depth of last impulses (0..1)")
    grinder_ratio: float = Field(..., description="Share of 'grind' bars (0..1)")

    # Orderbook / tape
    depth_usd_5bps: float = Field(..., description="USD depth within ±5 bps from mid (aggregated)")
    imbalance_sigma_hits_60m: int = Field(..., description="Times top-10 OB imbalance exceeded 2σ in last hour")

    # Diagnostics
    ws_lag_ms: Optional[int] = Field(None, description="Estimated WS lag for this symbol")
    stale_sec: Optional[int] = Field(None, description="Age of latest data snapshot in seconds")

    model_config = ConfigDict(extra="ignore")


class FeeInfo(BaseModel):
    """Per-symbol/venue fees; maker/taker may be None if unknown."""
    maker: Optional[float] = Field(None, description="Maker fee (fraction, e.g. 0.0005 for 0.05%)")
    taker: Optional[float] = Field(None, description="Taker fee (fraction)")
    zero_maker: bool = Field(False, description="True if maker fee is exactly 0.0")


class FeatureSnapshot(BaseModel):
    ts: int = Field(..., description="Unix epoch ms of snapshot")
    venue: str = Field(..., description="Exchange/venue identifier, e.g. 'gate' or 'mexc'")
    symbol: str
    preset: str = Field(..., description="scanner preset used (conservative/balanced/aggressive)")
    metrics: Metrics
    score: int = Field(..., ge=0, le=100)
    tier: Tier
    reasons: List[str] = Field(default_factory=list, description="Human-readable gates/flags leading to tiering")
    stale: bool = Field(False, description="True if data deemed stale")

    # Fees can come as a dict; Pydantic will coerce it into FeeInfo.
    fees: FeeInfo = Field(default_factory=FeeInfo, description="Maker/Taker fees and zero-maker flag")

    model_config = ConfigDict(extra="ignore")

    @field_validator("tier")
    @classmethod
    def _normalize_tier(cls, v: str) -> str:
        if v == "excluded":
            return "Excluded"
        return v


class ScannerTopResponse(BaseModel):
    ts: int = Field(..., description="Unix epoch ms of snapshot")
    preset: str = Field(..., description="scanner preset used (conservative/balanced/aggressive)")
    tierA: List[FeatureSnapshot] = Field(default_factory=list, description="Tier A candidates")
    tierB: List[FeatureSnapshot] = Field(default_factory=list, description="Tier B candidates")
    excluded: List[FeatureSnapshot] = Field(default_factory=list, description="Symbols excluded by filters")

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "ts": 1700000000000,
                    "preset": "balanced",
                    "tierA": [
                        {
                            "ts": 1700000000000,
                            "venue": "gate",
                            "symbol": "ETHUSDT",
                            "preset": "balanced",
                            "metrics": {
                                "usd_per_min": 25000.0,
                                "trades_per_min": 120.0,
                                "effective_spread_bps": 7.5,
                                "slip_bps_clip": 7.5,
                                "atr1m_pct": 0.004,
                                "spike_count_90m": 3,
                                "pullback_median_retrace": 0.35,
                                "grinder_ratio": 0.2,
                                "depth_usd_5bps": 15000.0,
                                "imbalance_sigma_hits_60m": 1,
                                "ws_lag_ms": 50,
                                "stale_sec": 2
                            },
                            "score": 87,
                            "tier": "A",
                            "reasons": ["spread_ok", "liquidity_high"],
                            "stale": False,
                            "fees": {
                                "maker": 0.0002,
                                "taker": 0.0007,
                                "zero_maker": False
                            }
                        }
                    ],
                    "tierB": [
                        {
                            "ts": 1700000000000,
                            "venue": "mexc",
                            "symbol": "BTCUSDT",
                            "preset": "balanced",
                            "metrics": {
                                "usd_per_min": 9000.0,
                                "trades_per_min": 35.0,
                                "effective_spread_bps": 15.0,
                                "slip_bps_clip": 15.0,
                                "atr1m_pct": 0.006,
                                "spike_count_90m": 1,
                                "pullback_median_retrace": 0.3,
                                "grinder_ratio": 0.25,
                                "depth_usd_5bps": 5000.0,
                                "imbalance_sigma_hits_60m": 0,
                                "ws_lag_ms": 120,
                                "stale_sec": 4
                            },
                            "score": 62,
                            "tier": "B",
                            "reasons": ["spread_wide", "moderate_liquidity"],
                            "stale": False,
                            "fees": {
                                "maker": 0.0003,
                                "taker": 0.0009,
                                "zero_maker": False
                            }
                        }
                    ],
                    "excluded": []
                }
            ]
        }
    )
