from __future__ import annotations

from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

# Accept both lowercase and capitalized to be router/tiering-proof
Tier = Literal["A", "B", "Excluded", "excluded"]


# ──────────────────────── Helpers ────────────────────────

class DepthAtBps(BaseModel):
    """Bid/Ask USD depth aggregated within ±N bps from mid."""
    bid_usd: Optional[float] = Field(None, description="USD depth on bid side within ±N bps from mid")
    ask_usd: Optional[float] = Field(None, description="USD depth on ask side within ±N bps from mid")

    model_config = ConfigDict(extra="ignore")


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

    # Rotation queue (set when rotation=true)
    queue: Optional[List[str]] = Field(None, description="Top-3 symbols by score across the whole payload")

    # Depth (±bps windows) — legacy flat fields
    depth5_bid_usd: Optional[float] = Field(None, description="USD depth on bid side within ±5 bps from mid")
    depth5_ask_usd: Optional[float] = Field(None, description="USD depth on ask side within ±5 bps from mid")
    depth10_bid_usd: Optional[float] = Field(None, description="USD depth on bid side within ±10 bps from mid")
    depth10_ask_usd: Optional[float] = Field(None, description="USD depth on ask side within ±10 bps from mid")
    # Note: routers may emit additional depth fields like depth3_bid_usd; these will be ignored unless added here.

    # Depth map (preferred) — NEW, backward-compatible
    depth_at_bps: Optional[Dict[int, DepthAtBps]] = Field(
        None,
        description=(
            "Map of bps level → {bid_usd, ask_usd}. "
            "Allows arbitrary levels (e.g., 5, 10). Supersedes legacy depth5/depth10 fields."
        ),
    )

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

    # Candle-derived extras (present when fetch_candles=true)
    atr1m_pct: Optional[float] = Field(None, description="ATR(20) on 1m bars divided by last close (fraction, e.g. 0.0042)")
    spike_count_90m: Optional[int] = Field(None, description="Count of spike candles in last 90 minutes")
    pullback_median_retrace: Optional[float] = Field(None, description="Median retrace depth of last impulses (0..1)")
    grinder_ratio: Optional[float] = Field(None, description="Share of 'grind' bars (0..1)")
    imbalance_sigma_hits_60m: Optional[int] = Field(None, description="Times top-10 OB imbalance exceeded 2σ in last hour")

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
                    "depth_at_bps": {
                        5: {"bid_usd": 5980.23, "ask_usd": 5668.93},
                        10: {"bid_usd": 16326.15, "ask_usd": 16616.41}
                    },
                    "eff_spread_bps": 2.9691,
                    "eff_spread_pct": 0.029691,
                    "eff_spread_abs": 1.33,
                    "eff_spread_bps_taker": 2.9691,
                    "eff_spread_pct_taker": 0.029691,
                    "eff_spread_abs_taker": 1.33,
                    "eff_spread_bps_maker": 2.5691,
                    "eff_spread_pct_maker": 0.025691,
                    "eff_spread_abs_maker": 1.15,
                    "atr1m_pct": 0.0042,
                    "spike_count_90m": 3,
                    "pullback_median_retrace": 0.34,
                    "grinder_ratio": 0.21,
                    "imbalance_sigma_hits_60m": 1,
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
                    "depth10_bid_usd": 11781.28,
                    "depth10_ask_usd": 15180.25,
                    "depth_at_bps": {
                        5: {"bid_usd": 6358.23, "ask_usd": 7924.45},
                        10: {"bid_usd": 11781.28, "ask_usd": 15180.25}
                    },
                    "eff_spread_bps": 2.5790,
                    "eff_spread_pct": 0.02579,
                    "eff_spread_abs": 0.000025,
                    "eff_spread_bps_taker": 2.5790,
                    "eff_spread_pct_taker": 0.02579,
                    "eff_spread_abs_taker": 0.000025,
                    "eff_spread_bps_maker": 2.1790,
                    "eff_spread_pct_maker": 0.02179,
                    "eff_spread_abs_maker": 0.000021,
                    "atr1m_pct": 0.0031,
                    "spike_count_90m": 2,
                    "pullback_median_retrace": 0.30,
                    "grinder_ratio": 0.19,
                    "imbalance_sigma_hits_60m": 0,
                    "score": 12.73,
                    "reason": "ok",
                    "reasons_all": ["fallback:tape_5m"]
                }
            ]
        },
    )

    # ─────────────── Normalizers & Back-compat glue ───────────────

    @staticmethod
    def _get_depth_level(dm: Any, level: int) -> Optional[Dict[str, Any]]:
        """
        Safely fetch a depth entry for either int or string key ("5"/5).
        """
        if not isinstance(dm, dict):
            return None
        v = dm.get(level) if level in dm else dm.get(str(level))
        return v if isinstance(v, dict) else None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, v: Any) -> Any:
        """
        Back-compat:
        - Build depth_at_bps from legacy depth5/10 fields if not provided.
        - Also backfill legacy fields from depth_at_bps if those are missing.
        - Normalize effective-spread aliases (eff_taker_bps/eff_maker_bps).
        - Default eff_spread_bps to taker variant if absent.
        """
        if not isinstance(v, dict):
            return v

        out = dict(v)

        # ---- depth map <-> legacy sync
        depth_map = out.get("depth_at_bps")
        d5b, d5a = out.get("depth5_bid_usd"), out.get("depth5_ask_usd")
        d10b, d10a = out.get("depth10_bid_usd"), out.get("depth10_ask_usd")

        if depth_map is None:
            dm: Dict[int, Dict[str, Optional[float]]] = {}
            if d5b is not None or d5a is not None:
                dm[5] = {"bid_usd": d5b, "ask_usd": d5a}
            if d10b is not None or d10a is not None:
                dm[10] = {"bid_usd": d10b, "ask_usd": d10a}
            if dm:
                out["depth_at_bps"] = dm
        else:
            # Backfill legacy fields if missing but map has 5/10. (fix operator precedence)
            v5 = cls._get_depth_level(depth_map, 5)
            if (d5b is None or d5a is None) and v5 is not None:
                out.setdefault("depth5_bid_usd", v5.get("bid_usd"))
                out.setdefault("depth5_ask_usd", v5.get("ask_usd"))

            v10 = cls._get_depth_level(depth_map, 10)
            if (d10b is None or d10a is None) and v10 is not None:
                out.setdefault("depth10_bid_usd", v10.get("bid_usd"))
                out.setdefault("depth10_ask_usd", v10.get("ask_usd"))

        # ---- effective spread alias normalization
        eff_taker_bps = out.get("eff_taker_bps")
        eff_maker_bps = out.get("eff_maker_bps")

        if out.get("eff_spread_bps_taker") is None and eff_taker_bps is not None:
            out["eff_spread_bps_taker"] = eff_taker_bps
        if out.get("eff_spread_bps_maker") is None and eff_maker_bps is not None:
            out["eff_spread_bps_maker"] = eff_maker_bps

        if out.get("eff_spread_bps") is None and out.get("eff_spread_bps_taker") is not None:
            out["eff_spread_bps"] = out.get("eff_spread_bps_taker")

        return out

    @field_validator("depth_at_bps", mode="before")
    @classmethod
    def _coerce_depth_map(cls, v: Any) -> Any:
        """
        Accept both {"5": {...}} and {5: {...}}, coerce to int keys and DepthAtBps values.
        Drop malformed entries gracefully.
        """
        if v is None:
            return v
        if not isinstance(v, dict):
            # Caller passed a list/tuple/etc — ignore rather than fail hard.
            return None

        coerced: Dict[int, Dict[str, Any]] = {}
        for k, raw in v.items():
            try:
                ik = int(k)
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            # only keep known fields; pydantic will coerce to DepthAtBps
            coerced[ik] = {"bid_usd": raw.get("bid_usd"), "ask_usd": raw.get("ask_usd")}
        return coerced


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
