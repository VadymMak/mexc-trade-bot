# app/services/market_scanner.py
"""
Market Scanner for Gate.io and MEXC (spot).
Stage 1: 24h stats + spread screening
Stage 2: lightweight depth/tape enrichments via REST with short timeouts
Optional: candles_cache enrich (atr1m_pct, spike_count_90m, etc.) via fetch_candles=True
Scoring: volume/depth/spread with a few optional pattern proxies
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field, asdict
from statistics import stdev
from typing import Any, Dict, List, Optional, Tuple, Sequence, Awaitable, Callable
from contextlib import suppress
import random
import logging
from decimal import Decimal, ROUND_HALF_UP  # NEW: for half-up rounding

import httpx

from app.config.settings import settings
from app.scoring.presets import PRESETS
from app.services.book_tracker import book_tracker  # noqa: F401

# Import *only* MEXC WS from ws_client; Gate WS is in its canonical module.
from app.market_data.ws_client import MEXCWebSocketClient  # noqa: F401 (used by other modules at runtime)
from app.market_data.gate_ws import GateWebSocketClient    # noqa: F401 (used by other modules at runtime)

# ─────────────────────────── optional candles cache export ───────────────────────────
try:
    from app.services.candles_cache import candles_cache  # type: ignore
except Exception:
    candles_cache: Any = {}  # harmless no-op fallback

log = logging.getLogger("scanner.gate")


# ─────────────────────────── dataclass ───────────────────────────

@dataclass
class ScanRow:
    # identity
    symbol: str  # e.g. "ETHUSDT"
    exchange: str = "gate"  # "gate" | "mexc"

    # top-of-book
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    spread_abs: float = 0.0
    spread_pct: float = 0.0
    spread_bps: float = 0.0

    # effective spreads (approx round-trip cost, bps)
    eff_spread_maker_bps: Optional[float] = None
    eff_spread_taker_bps: Optional[float] = None

    # 24h stats
    base_volume_24h: float = 0.0
    quote_volume_24h: float = 0.0

    # dynamic depth map: {bps: {"bid_usd": ..., "ask_usd": ...}}
    depth_at_bps: Dict[int, Dict[str, float]] = field(default_factory=dict)

    # Tape (≈60s)
    trades_per_min: float = 0.0
    usd_per_min: float = 0.0
    median_trade_usd: float = 0.0

    # fees
    maker_fee: Optional[float] = None  # fraction, e.g. 0.0005 = 5 bps
    taker_fee: Optional[float] = None
    zero_fee: Optional[bool] = None

    # Misc
    imbalance: float = 0.5
    ws_lag_ms: Optional[int] = None

    # explain
    reason: Optional[str] = None
    reasons_all: List[str] = field(default_factory=list)

    # final scoring
    score: Optional[float] = None

    # bot-enriched fields (existing)
    vol_pattern: Optional[int] = None       # 0-100 match score (e.g., stable_vol)
    net_profit_pct: Optional[float] = None  # effective profit after fees (%)
    liquidity_grade: Optional[str] = None   # 'A'/'B'/'C'
    dca_potential: Optional[int] = None     # 0-100 proxy score
    atr_proxy: Optional[float] = None       # std/ATR-like proxy
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)

    # candles_cache-enriched fields (optional)
    atr1m_pct: Optional[float] = None
    spike_count_90m: Optional[int] = None
    pullback_median_retrace: Optional[float] = None
    grinder_ratio: Optional[float] = None
    range_stable_pct: Optional[float] = None
    bars_1m: Optional[int] = None
    last_candle_ts: Optional[int] = None

    # --- Compatibility aliases (read-only) ---
    @property
    def eff_spread_bps_taker(self) -> Optional[float]:
        return self.eff_spread_taker_bps

    @property
    def eff_spread_bps_maker(self) -> Optional[float]:
        return self.eff_spread_maker_bps

    # JSON compatibility for CLI/table printers
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["eff_taker_bps"] = self.eff_spread_taker_bps
        d["eff_maker_bps"] = self.eff_spread_maker_bps
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ScanRow":
        if "eff_taker_bps" in d and "eff_spread_taker_bps" not in d:
            d["eff_spread_taker_bps"] = d.get("eff_taker_bps")
        if "eff_maker_bps" in d and "eff_spread_maker_bps" not in d:
            d["eff_spread_maker_bps"] = d.get("eff_maker_bps")
        return ScanRow(**{k: v for k, v in d.items() if k in ScanRow.__dataclass_fields__})


# ─────────────────────────── tiny cache ──────────────────────────

_CACHE: Dict[str, Tuple[float, List[ScanRow]]] = {}
try:
    _CACHE_TTL = float(getattr(settings, "scanner_cache_ttl", 20.0) or 20.0)
except Exception:
    _CACHE_TTL = 20.0

_GATE_FEE_CACHE: dict = {"ts": 0.0, "map": {}}  # ts = monotonic(), map = { "BTC_USDT": (maker, taker), "BTCUSDT": (...), ... }
_GATE_FEE_TTL_SEC: float = 600.0  # 10 minutes


# ─────────────────────────── helpers ─────────────────────────────

def _round2_half_up(x: float) -> float:
    """Round to 2 decimals using bankers-safe HALF_UP rule."""
    try:
        return float(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return float(f"{x:.2f}")

def _is_demo_mode() -> bool:
    try:
        m = getattr(settings, "active_mode", None) or getattr(settings, "account_mode", None)
        return str(m).lower() in {"paper", "demo", "test", "testnet"}
    except Exception:
        return False


def _gate_rest_base() -> str:
    # ✅ TEMPORARY FIX: Always use production API (public, no auth, much faster than testnet)
    # Production API is stable and doesn't require authentication for public endpoints
    return "https://api.gateio.ws/api/v4"
    
    # Original code (commented out - uncomment when testnet performance improves):
    # if _is_demo_mode():
    #     return getattr(settings, "gate_testnet_rest_base", None) or "https://api-testnet.gateapi.io/api/v4"
    # return getattr(settings, "gate_rest_base", None) or "https://api.gateio.ws/api/v4"


def _mexc_rest_base() -> str:
    if _is_demo_mode():
        tb = getattr(settings, "mexc_testnet_rest_base", None)
        if tb:
            return tb
    return getattr(settings, "mexc_rest_base", None) or "https://api.mexc.com"


def _coalesce_symbol(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    for sep in (" ", "/", "-", "_"):
        s = s.replace(sep, "")
    return s.upper()


def _to_pair(sym: str, quote: Optional[str] = None) -> str:
    """
    Normalize into 'BASE_QUOTE' with a single underscore.
    Prefers settings.quote (or provided quote), accepts BTCUSDT/BTC_USDT/BTC/USDT.
    """
    s = _coalesce_symbol(sym)
    q_pref = (quote or getattr(settings, "quote", "USDT") or "USDT").upper()
    common_quotes = [q_pref, "USDT", "USDC", "FDUSD", "BUSD", "USD", "BTC", "ETH"]

    for q in common_quotes:
        if s.endswith(q) and len(s) > len(q):
            base = s[: -len(q)]
            return f"{base}_{q}"

    # Honor explicit underscore in original as a fallback
    if "_" in (sym or ""):
        parts = str(sym).strip().upper().replace("-", "_").replace("/", "_").split("_")
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]}_{parts[1]}"

    # Fallback: attach preferred quote
    return f"{s}_{q_pref}" if s else f"_{q_pref}"

def _short(s: str, n: int = 400) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"...(+{len(s)-n} more)"

def _mk_gate_hooks(enabled: bool):
    if not enabled:
        return None
    async def _on_request(request: httpx.Request):
        try:
            log.debug("[gate-http] → %s %s?%s", request.method, request.url.path, request.url.query.decode())
        except Exception:
            pass
    async def _on_response(response: httpx.Response):
        try:
            # DO NOT log auth headers; only metadata + short body
            body = ""
            try:
                body = response.text
            except Exception:
                pass
            log.debug("[gate-http] ← %s %s %s in %.1fms | %s",
                      response.request.method,
                      response.request.url.path,
                      response.status_code,
                      (response.elapsed.total_seconds() * 1000.0 if response.elapsed else -1.0),
                      _short(body))
        except Exception:
            pass
    return {"request": [_on_request], "response": [_on_response]}



def _split_pair(pair: str) -> Tuple[str, str]:
    """
    Accepts either BASE_QUOTE or BASEQUOTE. Returns (BASE, QUOTE).
    Prefers settings.quote if it matches the tail.
    """
    if not pair:
        return "", ""
    p = str(pair).upper().strip()
    if "_" in p:
        b, q = p.split("_", 1)
        return b or "", q or ""
    q_pref = (getattr(settings, "quote", "USDT") or "USDT").upper()
    for q in [q_pref, "USDT", "USDC", "FDUSD", "BUSD", "USD", "BTC", "ETH"]:
        if p.endswith(q) and len(p) > len(q):
            return p[: -len(q)], q
    return p, ""


def _from_pair(pair: str) -> str:
    return pair.replace("_", "").upper()

# --- public, test-friendly aliases for private helpers ---
def compute_vol_stability(data, *, is_candles: bool = False, exchange: str = "gate") -> int:
    return _compute_vol_stability(data, is_candles=is_candles, exchange=exchange)

def compute_volatility_proxy(data, *, is_candles: bool = False, exchange: str = "gate") -> float:
    return _compute_volatility_proxy(data, is_candles=is_candles, exchange=exchange)

def _mk_mexc_hooks(enabled: bool):
    """HTTP event hooks for MEXC REST debugging (mirrors Gate logic)."""
    if not enabled:
        return None
    async def _on_request(request: httpx.Request):
        try:
            log.debug("[mexc-http] → %s %s?%s", request.method, request.url.path, request.url.query.decode())
        except Exception:
            pass
    async def _on_response(response: httpx.Response):
        try:
            body = ""
            try:
                body = response.text
            except Exception:
                pass
            log.debug("[mexc-http] ← %s %s %s in %.1fms | %s",
                      response.request.method,
                      response.request.url.path,
                      response.status_code,
                      (response.elapsed.total_seconds() * 1000.0 if response.elapsed else -1.0),
                      _short(body))
        except Exception:
            pass
    return {"request": [_on_request], "response": [_on_response]}

def _looks_like_leveraged(base: str) -> bool:
    b = base.upper()
    return any(sfx in b for sfx in ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR"))


def _is_stable(ccy: str) -> bool:
    return ccy.upper() in {"USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD"}


async def _gate_fetch_fee_map(cli) -> Dict[str, tuple]:
    """
    Returns dict:
      "BASE_QUOTE" -> (maker_fee, taker_fee)
      "BASEQUOTE"  -> (maker_fee, taker_fee)
    via Gate /spot/currency_pairs. No auth needed.
    Handles both production and testnet API formats.
    """
    import time as _pytime
    now = _pytime.monotonic()

    cached_ts = _GATE_FEE_CACHE.get("ts", 0.0)
    cached_map = _GATE_FEE_CACHE.get("map") or {}
    if cached_map and (now - cached_ts <= _GATE_FEE_TTL_SEC):
        log.debug(f"Gate fee map: using cache with {len(cached_map)} entries")
        return cached_map

    data = None
    try:
        log.info("Fetching Gate.io fee map from /spot/currency_pairs...")
        r = await _with_timeout(cli.get("/spot/currency_pairs", headers={"Accept": "application/json"}), 5.0, default=None)
        if r is not None and r.status_code == 200:
            data = r.json()
            log.info(f"Gate fee map response: {type(data)} with {len(data) if isinstance(data, list) else 0} items")
        else:
            log.warning("Gate fee-map HTTP status=%s body=%s", getattr(r, "status_code", None), getattr(r, "text", None)[:200] if r else None)
    except Exception as e:
        log.error("Gate fee-map fetch error: %s", e, exc_info=True)

    fmap: Dict[str, tuple] = {}
    if isinstance(data, list):
        # ✅ Debug first item structure
        if len(data) > 0:
            import json
            log.info(f"[DEBUG] First fee map item: {json.dumps(data[0], indent=2)}")
        
        for row in data:
            try:
                # ✅ Handle both testnet and production API formats
                
                # Try production format first (has "id" and separate maker/taker)
                pair = str(row.get("id") or "").upper()
                mf_raw = row.get("maker_fee_rate") or row.get("maker_fee")
                tf_raw = row.get("taker_fee_rate") or row.get("taker_fee")
                
                # If not found, try testnet format (has "base"+"quote" and single "fee")
                if not pair or mf_raw is None or tf_raw is None:
                    base = str(row.get("base") or "").upper()
                    quote = str(row.get("quote") or "").upper()
                    
                    if base and quote:
                        pair = f"{base}_{quote}"
                        
                        # Testnet uses single "fee" field (as percentage string like "0.2")
                        fee_raw = row.get("fee")
                        if fee_raw is not None:
                            # Convert percentage to fraction (0.2 → 0.002)
                            fee_fraction = float(fee_raw) / 100.0
                            mf_raw = fee_fraction
                            tf_raw = fee_fraction
                
                if mf_raw is None or tf_raw is None:
                    continue
                
                mf = float(mf_raw)
                tf = float(tf_raw)
                
                if pair and mf >= 0 and tf >= 0:
                    fmap[pair] = (mf, tf)
                    fmap[pair.replace("_", "")] = (mf, tf)
                    
                    # ✅ Log first 3 pairs for verification
                    if len(fmap) <= 6:  # 3 pairs × 2 entries
                        log.debug(f"Fee map entry: {pair} → maker={mf:.6f}, taker={tf:.6f}")
            except Exception as ex:
                log.warning(f"Failed to parse fee for row: {ex}")
                continue
    else:
        log.warning(f"Gate fee-map response is not a list, got: {type(data)}")
        if cached_map:
            log.info("Gate fee-map: reuse cached=%d entries due to non-list response", len(cached_map))
            return cached_map

    if fmap:
        _GATE_FEE_CACHE["ts"] = now
        _GATE_FEE_CACHE["map"] = fmap
        log.info("Gate fee map loaded: %d entries (examples: BTC_USDT=%s, ETH_USDT=%s, ALEPH_USDT=%s)", 
                 len(fmap), 
                 fmap.get("BTC_USDT"), 
                 fmap.get("ETH_USDT"),
                 fmap.get("ALEPH_USDT"))
    else:
        log.error("Gate fee map is empty (no cache to reuse)")

    return fmap



def _absorption_usd_in_band(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    mid: float,
    x_bps: float,
) -> Tuple[float, float]:
    """USD sum on each side within ±x_bps from mid. Returns (bid_usd, ask_usd)."""
    if not (mid > 0 and x_bps > 0):
        return 0.0, 0.0
    band = x_bps / 1e4
    bid_floor = mid * (1.0 - band)
    ask_cap = mid * (1.0 + band)

    bid_usd = 0.0
    for p, q in bids:
        with suppress(Exception):
            if p <= 0 or q <= 0:
                continue
            if p < bid_floor:
                break
            bid_usd += p * q

    ask_usd = 0.0
    for p, q in asks:
        with suppress(Exception):
            if p <= 0 or q <= 0:
                continue
            if p > ask_cap:
                break
            ask_usd += p * q
    return bid_usd, ask_usd


def _imbalance_from_sizes(bid_qty: float, ask_qty: float) -> float:
    try:
        b = float(bid_qty or 0.0)
        a = float(ask_qty or 0.0)
        s = b + a
        return (b / s) if s > 0 else 0.5
    except Exception:
        return 0.5


def _to_bps(fee_frac: Optional[float]) -> Optional[float]:
    if fee_frac is None:
        return None
    with suppress(Exception):
        return float(fee_frac) * 1e4
    return None


def _calc_effective_spreads(
    spread_bps: float,
    maker_fee: Optional[float],
    taker_fee: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """
    Approx round-trip cost:
      • taker: spread + 2 * taker_fee_bps
      • maker: max(spread - 2 * maker_fee_bps, 0)
    """
    maker_bps = _to_bps(maker_fee)
    taker_bps = _to_bps(taker_fee)
    eff_m = max(spread_bps - 2.0 * maker_bps, 0.0) if maker_bps is not None else None
    eff_t = spread_bps + 2.0 * taker_bps if taker_bps is not None else None
    return eff_m, eff_t


def _apply_stage1_fields_and_effective(row: ScanRow) -> None:
    row.last = row.last or (row.bid + row.ask) * 0.5
    row.spread_abs = max(0.0, (row.ask - row.bid))
    mid = (row.bid + row.ask) * 0.5
    row.spread_pct = (row.spread_abs / mid) * 100.0 if mid > 0 else 0.0
    row.spread_bps = (row.spread_abs / mid) * 1e4 if mid > 0 else 0.0
    eff_m, eff_t = _calc_effective_spreads(row.spread_bps, row.maker_fee, row.taker_fee)
    row.eff_spread_maker_bps = eff_m
    row.eff_spread_taker_bps = eff_t
    row.net_profit_pct = (eff_m / 100.0 if eff_m is not None else 0.0) + (0.1 if row.zero_fee else 0.0)

def _log_fee_source(row: ScanRow, source: str, explain: bool) -> None:
    """Log fee application source for observability."""
    if not explain:
        return
    
    badge = f"fees:{source}"
    if source == "map" and row.zero_fee:
        badge += "+zero"
    
    # Avoid duplicates
    if badge not in row.reasons_all:
        row.reasons_all.append(badge)

def _build_depth_map(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    mid: float,
    levels: List[int]
) -> Dict[int, Dict[str, float]]:
    """
    Build depth_at_bps map for given BPS levels.
    Returns empty dict if inputs are invalid.
    """
    depth_map: Dict[int, Dict[str, float]] = {}
    
    if not (bids and asks and mid > 0):
        return depth_map
    
    for level_bps in levels:
        bid_usd, ask_usd = _absorption_usd_in_band(bids, asks, mid, float(level_bps))
        depth_map[level_bps] = {
            "bid_usd": _round2_half_up(bid_usd),
            "ask_usd": _round2_half_up(ask_usd),
        }
    
    return depth_map


def _log1p_safe(x: float) -> float:
    with suppress(Exception):
        return math.log1p(max(0.0, float(x)))
    return 0.0


def _score_w(key: str, default: float) -> float:
    with suppress(Exception):
        upper = getattr(settings, f"SCORE_W_{key.upper()}", None)
        if upper is not None and str(upper) != "":
            return float(upper)
    with suppress(Exception):
        lower = getattr(settings, f"score_w_{key.lower()}", None)
        if lower is not None and str(lower) != "":
            return float(lower)
    return float(default)


def _score_row(row: ScanRow, depth_key_bps: int = 5) -> float:
    w_usdpm = _score_w("USD_PER_MIN", 1.0)
    w_depth = _score_w("DEPTH", 0.7)
    w_spread = _score_w("SPREAD", 0.5)
    w_eff = _score_w("EFF", 0.6)
    w_vol = _score_w("VOL_PATTERN", 0.4)
    w_dca = _score_w("DCA", 0.5)
    w_atr = _score_w("ATR", 0.3)

    d = row.depth_at_bps.get(depth_key_bps, {"bid_usd": 0.0, "ask_usd": 0.0})
    depth_min_side = min(d["bid_usd"], d["ask_usd"])
    usd_term = w_usdpm * _log1p_safe(row.usd_per_min)
    depth_term = w_depth * _log1p_safe(depth_min_side)
    eff_bps = row.eff_spread_taker_bps if row.eff_spread_taker_bps is not None else row.spread_bps
    spread_pen = w_spread * (row.spread_bps / 10.0) + w_eff * (eff_bps / 10.0)

    vol_term = w_vol * (row.vol_pattern or 0) / 100.0
    dca_term = w_dca * (row.dca_potential or 0) / 100.0
    atr_pen = w_atr * _log1p_safe(row.atr_proxy or 0) / 10.0

    spread_bonus = 0.1 * max(0.0, (10.0 - row.spread_bps) / 10.0)

    return usd_term + depth_term + vol_term + dca_term - spread_pen - atr_pen + spread_bonus


def _compute_vol_stability(data: Any, *, is_candles: bool = False, exchange: str = "gate") -> int:
    vols: List[float] = []
    if is_candles:
        if exchange == "gate":
            # Gate candle: [t, v, c, h, l, o] → volume at index 1
            for c in (data or [])[-20:]:
                with suppress(Exception):
                    if len(c) > 1:
                        v = float(c[1])
                        if v > 0:
                            vols.append(v)
        else:
            # MEXC candle: [otime_ms, o, h, l, c, v, ...] → volume at index 5
            for c in (data or [])[-20:]:
                with suppress(Exception):
                    if len(c) > 5:
                        v = float(c[5])
                        if v > 0:
                            vols.append(v)
    else:
        for x in (data or [])[-20:]:
            with suppress(Exception):
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    qty = float(x[1])
                else:
                    qty = float(x.get("qty") or x.get("amount") or 0.0)
                if qty > 0:
                    vols.append(qty)

    n = len(vols)
    if n < 10:
        return 50
    mean_v = sum(vols) / n if n else 0
    if mean_v <= 0:
        return 50
    std_v = stdev(vols) if n >= 2 else 0.0
    ratio = std_v / mean_v
    score = 50 + int((1 - min(ratio, 1)) * 50)
    if ratio < 0.5:
        score += 20
    return min(100, max(0, score))


def _compute_volatility_proxy(data: Any, *, is_candles: bool = False, exchange: str = "gate") -> float:
    if is_candles:
        trs: List[float] = []
        if len(data or []) >= 2:
            for i in range(1, min(20, len(data))):
                with suppress(Exception):
                    if exchange == "gate":
                        # Gate: prev close at index 2; high=3, low=4
                        prev_c = float(data[i - 1][2])
                        h = float(data[i][3])
                        l = float(data[i][4])
                    else:
                        # MEXC: prev close at index 4; high=2, low=3
                        prev_c = float(data[i - 1][4])
                        h = float(data[i][2])
                        l = float(data[i][3])
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    trs.append(tr)
        if trs:
            return sum(trs) / len(trs)

        closes: List[float] = []
        if exchange == "gate":
            # Gate close at index 2
            closes = [float(c[2]) for c in (data or [])[-20:] if len(c) > 2 and float(c[2]) > 0]
        else:
            closes = [float(c[4]) for c in (data or [])[-20:] if len(c) > 4 and float(c[4]) > 0]
        if len(closes) < 5:
            return 0.0
        mean_p = sum(closes) / len(closes)
        return math.sqrt(sum((p - mean_p) ** 2 for p in closes) / len(closes))
    else:
        prices: List[float] = []
        for x in (data or [])[-20:]:
            with suppress(Exception):
                if isinstance(x, (list, tuple)) and len(x) >= 1:
                    p = float(x[0])
                else:
                    p = float(x.get("price") or 0.0)
                if p > 0:
                    prices.append(p)
        if len(prices) < 2:
            return 0.0
        deltas = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        if deltas:
            return sum(deltas) / len(deltas)
        mean_p = sum(prices) / len(prices)
        return math.sqrt(sum((p - mean_p) ** 2 for p in prices) / len(prices))


def _compute_dca_potential(
    row: ScanRow,
    trades: List[Dict[str, Any]],
    candles: Optional[List[List[Any]]] = None,
    exchange: str = "gate"
) -> int:
    med = row.median_trade_usd or 0.0
    usdpm = row.usd_per_min or 0.0
    base_score = (med / usdpm * 100) if usdpm > 0 else 0

    closes: List[float] = []
    if candles and len(candles) >= 5:
        if exchange == "gate":
            # Gate close index 2
            closes = [float(c[2]) for c in candles[-10:] if len(c) > 2 and float(c[2]) > 0]
        else:
            closes = [float(c[4]) for c in candles[-10:] if len(c) > 4 and float(c[4]) > 0]
    if not closes:
        for x in (trades or [])[-10:]:
            with suppress(Exception):
                p = float(x.get("price") or 0.0)
                if p > 0:
                    closes.append(p)
    if len(closes) >= 5:
        mean_c = sum(closes) / len(closes)
        std_c = math.sqrt(sum((c - mean_c) ** 2 for c in closes) / len(closes))
        pct_std = (std_c / mean_c * 100) if mean_c > 0 else float("inf")
        if pct_std < 0.1:
            base_score += 30
    return max(0, min(100, int(base_score)))


def _compute_liquidity_grade(depth_min_usd: float) -> str:
    if depth_min_usd > 5000:
        return "A"
    if depth_min_usd > 2000:
        return "B"
    return "C"

def _compute_activity_ratio(
    usd_per_min: float,
    depth5_min_side: float,
    default: float = 0.0,
) -> float:
    """
    Compute activity ratio: usd_per_min / depth5_min_side.
    Protected from ZeroDivisionError.
    
    Args:
        usd_per_min: USD turnover per minute
        depth5_min_side: Minimum depth at ±5bps (bid or ask)
        default: Value to return if division by zero
        
    Returns:
        Activity ratio or default value
    """
    # Защита от None и некорректных значений
    if depth5_min_side is None or depth5_min_side <= 0:
        return default
    
    if usd_per_min is None or usd_per_min < 0:
        return default
    
    try:
        ratio = usd_per_min / depth5_min_side
        # Дополнительная защита от inf/nan
        if not math.isfinite(ratio):
            return default
        return ratio
    except (ZeroDivisionError, TypeError, ValueError):
        return default


def _http_timeout(short: bool = False) -> httpx.Timeout:
    read_timeout = 8.0 if short else 12.0  # ← Changed to 8/12 (not 10/20!)
    return httpx.Timeout(connect=3.0, read=read_timeout, write=3.0, pool=2.0)  # ← connect 3s (not 5s)


async def _with_timeout(awaitable, seconds: float = 3.0, default=None):
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError:
        log.warning("⏳ REST timeout after %.1fs", seconds)
        return default
    except Exception as e:
        log.warning("⚠️ REST error: %s", e)
        return default



def _get_retry_attempts() -> int:
    """Get REST retry attempts from settings with fallback."""
    try:
        return int(getattr(settings, "rest_retry_attempts", 3))  # ← Keep at 3 (not 5)
    except Exception:
        return 3  # ← Keep at 3
    
def _get_backoff_base() -> float:
    """Get backoff base delay from settings."""
    try:
        return float(getattr(settings, "rest_backoff_base_sec", 0.5))  # ← 0.5 is OK
    except Exception:
        return 0.5  # ← 0.5 is OK

async def _retry(
    build_coro: Callable[[], Awaitable[Any]],
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    venue: str = "unknown",
    operation: str = "request",  # NEW parameter
) -> Any:
    """
    Retry HTTP requests with exponential backoff.
    
    Args:
        build_coro: Callable that returns a coroutine to execute
        max_retries: Maximum retry attempts (None = use settings)
        base_delay: Base delay for exponential backoff (None = use settings)
        venue: Venue name for logging
        operation: Operation being retried (e.g., "fetch_tickers", "fetch_depth")
        
    Returns:
        Result of the coroutine
        
    Raises:
        Last exception if all retries fail
    """
    if max_retries is None:
        max_retries = _get_retry_attempts()
    if base_delay is None:
        base_delay = _get_backoff_base()
    
    last_exc: Optional[BaseException] = None
    
    for attempt in range(max_retries):
        try:
            return await build_coro()
        except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ConnectError, httpx.RequestError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = (2 ** attempt) * base_delay + random.random() * 0.1
                log.debug(
                    f"Retry {attempt + 1}/{max_retries} for {venue}/{operation} "
                    f"after {delay:.2f}s: {type(e).__name__}"
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    f"All {max_retries} retry attempts failed for {venue}/{operation}: {e}"
                )
    
    raise last_exc or Exception(f"Retry failed for {venue}/{operation}")


# ─────────────────────── explain: reason classifier ───────────────────────

def _classify_reason(
    row: ScanRow,
    *,
    min_depth5_usd: float,
    min_trades_per_min: float,
    min_usd_per_min: float,
    spread_cap_bps: float,
    explain: bool,
) -> None:
    if row.reason == "partial" and explain:
        row.reasons_all.append("state:partial")

    d5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    depth5_min = float(min(d5.get("bid_usd", 0.0), d5.get("ask_usd", 0.0)))
    tpm = float(row.trades_per_min or 0.0)
    usdpm = float(row.usd_per_min or 0.0)

    if spread_cap_bps > 0 and row.spread_bps > spread_cap_bps:
        row.reason = "spread too wide"
        if explain:
            row.reasons_all.append(f"spread_bps:{row.spread_bps:.2f} > cap:{spread_cap_bps:.2f}")
        return

    if min_depth5_usd and depth5_min < min_depth5_usd:
        row.reason = "low depth"
        if explain:
            row.reasons_all.append(f"depth5_min:{depth5_min:.1f} < min:{min_depth5_usd:.1f}")
        return

    if min_usd_per_min and usdpm < min_usd_per_min:
        row.reason = "low turnover"
        if explain:
            row.reasons_all.append(f"usd_per_min:{usdpm:.2f} < min:{min_usd_per_min:.2f}")
        return

    if min_trades_per_min and tpm < min_trades_per_min:
        row.reason = "low tpm"
        if explain:
            row.reasons_all.append(f"trades_per_min:{tpm:.2f} < min:{min_trades_per_min:.2f}")
        return

    if row.liquidity_grade == "C" and explain:
        row.reasons_all.append(f"grade:{row.liquidity_grade} (depth5:{depth5_min:.1f})")

    if row.reason == "partial":
        return

    row.reason = "ok"
    if explain:
        row.reasons_all.append("state:ok")


# ─────────────────────────── Gate REST calls ─────────────────────

_AVAILABLE_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")

async def _gate_fetch_tickers(client: httpx.AsyncClient, quote: str) -> List[Dict[str, Any]]:
    async def fetch():
        r = await client.get("/spot/tickers", params={"currency_pair": "", "limit": 5000})
        r.raise_for_status()
        arr = r.json()
        if isinstance(arr, list):
            return [
                t for t in arr
                if isinstance(t, dict) and str(t.get("currency_pair", "")).upper().endswith(f"_{quote.upper()}")
            ]
        return []
    return await _retry(fetch) or []

async def _gate_fetch_order_book(client: httpx.AsyncClient, pair: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    async def fetch():
        r = await client.get("/spot/order_book", params={"currency_pair": pair, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    return await _retry(fetch) or None

async def _gate_fetch_trades(client: httpx.AsyncClient, pair: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    async def fetch():
        r = await client.get("/spot/trades", params={"currency_pair": pair, "limit": min(200, max(50, limit))})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch) or None

async def _gate_fetch_candles(client: httpx.AsyncClient, pair: str, interval: str = "1m", limit: int = 60) -> Optional[List[List[Any]]]:
    async def fetch():
        r = await client.get("/spot/candlesticks", params={"currency_pair": pair, "interval": interval, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch) or None


# ─────────────────────────── MEXC REST calls ─────────────────────

_MEXC_DEFAULT_MAKER = 0.0      # 0%
_MEXC_DEFAULT_TAKER = 0.0005   # 5 bps

async def _mexc_fetch_book_tickers(client: httpx.AsyncClient, quote: str) -> List[Dict[str, Any]]:
    async def fetch():
        r = await client.get("/api/v3/ticker/bookTicker")
        r.raise_for_status()
        arr = r.json()
        if not isinstance(arr, list):
            return []
        q = quote.upper()
        return [t for t in arr if isinstance(t, dict) and str(t.get("symbol", "")).upper().endswith(q)]
    return await _retry(fetch) or []

async def _mexc_fetch_24h(client: httpx.AsyncClient, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch 24h ticker for multiple symbols.
    Smart mode: batch if len(symbols) >= 20, individual otherwise.
    """
    res: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return res

    # Smart selection: batch for many symbols, individual for few
    use_batch = len(symbols) >= 20

    if use_batch:
        # BATCH MODE: get all tickers at once
        try:
            async def fetch():
                r = await client.get("/api/v3/ticker/24hr")  # No symbol param = ALL
                r.raise_for_status()
                return r.json()

            data = await _retry(fetch) or []

            if isinstance(data, list):
                # Filter to requested symbols
                target_set = {s.upper() for s in symbols}
                for item in data:
                    if isinstance(item, dict):
                        sym = str(item.get("symbol", "")).upper()
                        if sym in target_set:
                            res[sym] = item

            log.info(f"Fetched 24h ticker batch: {len(res)}/{len(symbols)} symbols")
        except Exception as e:
            log.warning("MEXC batch 24h failed: %s, falling back to individual", e)
            use_batch = False

    # Fallback: individual requests (only if batch is not used or failed)
    if not use_batch:
        sem = asyncio.Semaphore(15)

        async def _one(sym: str) -> None:
            async with sem:
                with suppress(Exception):
                    rr = await client.get("/api/v3/ticker/24hr", params={"symbol": sym})
                    if rr.status_code == 200:
                        j = rr.json()
                        if isinstance(j, dict):
                            res[sym] = j

        tasks = [asyncio.create_task(_one(s)) for s in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    return res

async def _mexc_fetch_depth(client: httpx.AsyncClient, symbol: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    async def fetch():
        r = await client.get("/api/v3/depth", params={"symbol": symbol, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    return await _retry(fetch) or None

async def _mexc_fetch_trades(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    async def fetch():
        r = await client.get("/api/v3/trades", params={"symbol": symbol, "limit": min(200, max(50, limit))})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch) or None

async def _mexc_fetch_candles(client: httpx.AsyncClient, symbol: str, interval: str = "1m", limit: int = 60) -> Optional[List[List[Any]]]:
    async def fetch():
        r = await client.get("/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch) or None


# ─────────────────────────── Gate scanner ───────────────

async def _scan_one_quote_gate(
    *,
    client: httpx.AsyncClient,
    quote: str,
    limit: int,
    min_quote_vol_usd: float,
    max_spread_bps: float,
    include_stables: bool,
    exclude_leveraged: bool,
    symbols: Optional[List[str]] = None,
) -> List[ScanRow]:
    tickers = await _gate_fetch_tickers(client, quote=quote)

    vol_candidates = [
        t for t in tickers
        if isinstance(t, dict) and str(t.get("currency_pair", "")).upper().endswith(f"_{quote.upper()}")
    ]

    if symbols:
        raw_targets = {s.strip().upper() for s in symbols if s and s.strip()}
        q_up = quote.upper()

        targets_flat: set[str] = set()   # e.g., ETHUSDT
        targets_pairs: set[str] = set()  # e.g., ETH_USDT

        for s in raw_targets:
            s_norm = s.replace("-", "").replace("/", "").upper()
            if not s_norm.endswith(q_up):
                s_norm = f"{s_norm}{q_up}"
            base = s_norm[:-len(q_up)]
            pair = f"{base}_{q_up}"
            targets_flat.add(s_norm)
            targets_pairs.add(pair)

        def _matches_symbol(t: Dict[str, Any]) -> bool:
            cp = str(t.get("currency_pair", "")).upper()
            nosep = cp.replace("_", "")
            return (cp in targets_pairs) or (nosep in targets_flat)

        vol_candidates = [t for t in vol_candidates if _matches_symbol(t)]

    def _sp(pair: str) -> Tuple[str, str]:
        b, q = _split_pair(pair)
        return b, q

    vol_candidates = [
        t for t in vol_candidates
        if (include_stables or not _is_stable(_sp(str(t.get("currency_pair", "")))[0]))
        and (not exclude_leveraged or not _looks_like_leveraged(_sp(str(t.get("currency_pair", "")))[0]))
    ]

    vol_candidates.sort(key=lambda t: float(t.get("quote_volume", 0)), reverse=True)
    if not symbols:
        vol_candidates = vol_candidates[:200]

    gate_maker_fee = getattr(settings, "gate_maker_fee", None)
    gate_taker_fee = getattr(settings, "gate_taker_fee", None)
    gate_zero_fee = getattr(settings, "gate_zero_fee", None)

    rows: List[ScanRow] = []
    for t in vol_candidates:
        with suppress(Exception):
            pair = str(t.get("currency_pair", ""))
            bid = float(t.get("highest_bid") or 0.0)
            ask = float(t.get("lowest_ask") or 0.0)
            last = float(t.get("last") or 0.0)
            base_vol = float(t.get("base_volume") or 0.0)
            quote_vol = float(t.get("quote_volume") or 0.0)
            if bid <= 0.0 or ask <= 0.0 or ask <= bid:
                continue
            if quote_vol < float(min_quote_vol_usd):
                continue

            row = ScanRow(
                symbol=_from_pair(pair),
                exchange="gate",
                bid=bid,
                ask=ask,
                last=last,
                base_volume_24h=base_vol,
                quote_volume_24h=quote_vol,
                # ENV defaults (can be overridden later by map)
                maker_fee=(float(gate_maker_fee) if gate_maker_fee not in (None, "") else None),
                taker_fee=(float(gate_taker_fee) if gate_taker_fee not in (None, "") else None),
                zero_fee=(bool(gate_zero_fee) if gate_zero_fee is not None else None),
            )
            _apply_stage1_fields_and_effective(row)
            if max_spread_bps > 0.0 and row.spread_bps > max_spread_bps:
                continue
            rows.append(row)

    rows.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
    return rows[: max(limit * 4, 50)]


async def scan_gate_quote(
    *,
    quote: str = "USDT",
    limit: int = 100,
    min_quote_vol_usd: float = 50_000,
    min_spread_pct: Optional[float] = None,
    max_spread_bps: Optional[float] = 10.0,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    depth_levels_bps: Sequence[int] | None = None,
    min_depth5_usd: float = 0.0,
    min_depth10_usd: float = 0.0,
    min_trades_per_min: float = 0.0,
    min_usd_per_min: float = 0.0,
    min_median_trade_usd: float = 0.0,
    min_vol_pattern: float = 0.0,
    max_atr_proxy: float = float("inf"),
    activity_ratio: float = 0.0,
    explain: bool = False,
    use_cache: bool = True,
    liquidity_test: bool = False,
    symbols: Optional[List[str]] = None,
    fetch_candles: bool = False,  # NEW
) -> List[ScanRow]:
    """
    Gate scanner: Stage1 (24h + spread) → Stage2 (depth/tape) with short timeouts.
    Supports quote="ALL".
    If fetch_candles=True — additionally fetches metrics from candles_cache.
    """
    import time as _pytime
    from contextlib import suppress
    from app.scoring.presets import get_preset

    limit = max(1, min(500, int(limit)))
    q_upper = quote.upper()
    levels = sorted({int(v) for v in (depth_levels_bps or [5, 10]) if int(v) > 0})
    if 5 not in levels:
        levels.append(5)
    if 10 not in levels:
        levels.append(10)
    levels.sort()

    if max_spread_bps is not None:
        spread_cap_bps = float(max_spread_bps)
    elif min_spread_pct is not None:
        spread_cap_bps = float(min_spread_pct) * 100.0
    else:
        spread_cap_bps = 10.0

    cache_key = (
        f"gate:{q_upper}:{limit}:{min_quote_vol_usd}:{spread_cap_bps}:{include_stables}:{exclude_leveraged}:"
        f"{tuple(levels)}:{min_depth5_usd}:{min_depth10_usd}:{min_trades_per_min}:{min_usd_per_min}:"
        f"{min_median_trade_usd}:{min_vol_pattern}:{max_atr_proxy}:{activity_ratio}:{int(explain)}:{int(liquidity_test)}"
        f":{tuple(sorted(symbols or []))}:{int(fetch_candles)}"
    )
    now = _pytime.monotonic()
    if use_cache and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts <= _CACHE_TTL:
            return data[:limit]

    base_url = _gate_rest_base()
    headers = {"Accept": "application/json", "User-Agent": "scanner/1.0"}
    # Enable HTTP debug if env flag OR explain=True
    debug_http_gate = bool(getattr(settings, "http_debug_gate", False) or explain)
    hooks = _mk_gate_hooks(debug_http_gate) or {}

    # IMPORTANT: wire event hooks so debug works
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=_http_timeout(False),
        event_hooks=hooks,
        limits=httpx.Limits(
            max_connections=100,        # ← ADD: Allow 100 total connections
            max_keepalive_connections=50  # ← ADD: Keep 50 alive
        ),
    ) as cli:
        # 1) prefetch fee map (non-fatal)
        fee_map = await _gate_fetch_fee_map(cli)
        try:
            log.info("gate fee map loaded: %d symbols", len(fee_map))
        except Exception:
            pass

        quotes_to_scan: List[str] = list(_AVAILABLE_QUOTES) if q_upper == "ALL" else [q_upper]

        # 2) Stage 1
        stage1_all: List[ScanRow] = []
        for q in quotes_to_scan:
            rows_q = await _scan_one_quote_gate(
                client=cli,
                quote=q,
                limit=limit,
                min_quote_vol_usd=min_quote_vol_usd,
                max_spread_bps=spread_cap_bps,
                include_stables=include_stables,
                exclude_leveraged=exclude_leveraged,
                symbols=symbols,
            )
            stage1_all.extend(rows_q)

        if not stage1_all:
            _CACHE[cache_key] = (now, [])
            return []

        stage1_all.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
        shortlist = stage1_all[: min(len(stage1_all), 100)]

        # 3) Stage 2 enrichment
        gate_concurrency = getattr(settings, "gate_scan_concurrency", 12)
        sem = asyncio.Semaphore(int(gate_concurrency))
        log.debug("Gate scanner: concurrency=%d, explain=%s", gate_concurrency, explain)
        _BAL = get_preset("balanced")  # for ATR placeholder (fraction units)
        preset_name = "balanced"  # default
        # Detect preset from caller if available (future: pass as param)
        _PRESET = get_preset(preset_name)

        async def _enrich_one(row: ScanRow) -> Optional[ScanRow]:
            async with sem:
                sym = row.symbol
                q = next((cand for cand in _AVAILABLE_QUOTES if sym.endswith(cand)), quotes_to_scan[0])
                pair = _to_pair(sym, quote=q)             # "BASE_QUOTE"
                pair_ccy = pair.replace("_", "")          # "BASEQUOTE"
                mid = (row.bid + row.ask) * 0.5

                # --- Fees: ENV defaults from stage-1 can be overridden by per-pair map
                applied_source = "env" if ((row.maker_fee is not None) or (row.taker_fee is not None)) else "none"
                mf_tf = fee_map.get(pair) or fee_map.get(pair_ccy) or fee_map.get(sym) or fee_map.get(sym.replace("_", ""))
                if mf_tf:
                    try:
                        row.maker_fee = float(mf_tf[0])
                        row.taker_fee = float(mf_tf[1])
                        row.zero_fee = bool(abs(row.maker_fee or 0.0) < 1e-12)
                        applied_source = "map"
                    except Exception:
                        # keep whatever was there (env or none)
                        pass

                # recompute effective spreads with final fees
                _apply_stage1_fields_and_effective(row)

                if getattr(settings, "http_debug_gate", False) or explain:
                    log.info("[fees] %s -> maker=%r taker=%r zero=%r source=%s",
                             sym, row.maker_fee, row.taker_fee, row.zero_fee, applied_source)
                
                # Track fee source using helper
                _log_fee_source(row, applied_source, explain)

                # --- order book & imbalance ---
                ob = await _with_timeout(_gate_fetch_order_book(cli, pair, limit=50), 5.0, default=None)
                bids: List[Tuple[float, float]] = []
                asks: List[Tuple[float, float]] = []
                imb = 0.5
                if isinstance(ob, dict):
                    for it in (ob.get("bids") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0:
                                bids.append((p, qt))
                    bids.sort(key=lambda x: x[0], reverse=True)
                    for it in (ob.get("asks") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0:
                                asks.append((p, qt))
                    asks.sort(key=lambda x: x[0])
                    if bids and asks:
                        mid = (bids[0][0] + asks[0][0]) * 0.5
                        imb = _imbalance_from_sizes(bids[0][1], asks[0][1])

                depth_map = _build_depth_map(bids, asks, mid, levels)

                # --- tape (1m, fallback 5m, then 24h) ---
                tr = await _with_timeout(_gate_fetch_trades(cli, pair, limit=120), 5.0, default=None)
                candles = await _with_timeout(_gate_fetch_candles(cli, pair, limit=60), 5.0, default=None)
                if candles is None and explain:
                    row.reasons_all.append("timeout:candles")

                def _calc_window(minutes: int) -> Tuple[float, float, float]:
                    if not isinstance(tr, list) or not tr:
                        return 0.0, 0.0, 0.0
                    now_ms = int(_pytime.time() * 1000)
                    wins_ms = minutes * 60_000
                    amts: List[float] = []
                    total_usd = 0.0
                    total_cnt = 0
                    for x in tr:
                        with suppress(Exception):
                            ts_ms = None
                            if "create_time_ms" in x:
                                ts_ms = int(float(x.get("create_time_ms", 0)))
                            elif "create_time" in x:
                                ts_ms = int(float(x.get("create_time", 0)) * 1000)
                            if ts_ms is None or now_ms - ts_ms > wins_ms:
                                continue
                            price = float(x.get("price") or 0.0)
                            amt = float(x.get("amount") or 0.0)
                            if price <= 0 or amt <= 0:
                                continue
                            notional = price * amt
                            if notional < 1.0:
                                continue
                            amts.append(notional)
                            total_usd += notional
                            total_cnt += 1
                    if total_cnt == 0:
                        return 0.0, 0.0, 0.0
                    amts.sort()
                    median = amts[len(amts) // 2]
                    return float(total_cnt) / float(minutes), float(total_usd) / float(minutes), float(median)

                tpm, usdpm, med = _calc_window(1)
                if tpm == 0.0 and usdpm == 0.0:
                    tpm, usdpm, med = _calc_window(5)
                    if (tpm > 0.0 or usdpm > 0.0) and explain:
                        row.reasons_all.append("fallback:tape_5m")

                if (tpm == 0.0 and usdpm == 0.0) and isinstance(tr, list) and tr:
                    with suppress(Exception):
                        ts = []
                        for x in tr:
                            if "create_time_ms" in x:
                                ts.append(int(float(x["create_time_ms"])))
                            elif "create_time" in x:
                                ts.append(int(float(x["create_time"]) * 1000))
                        if len(ts) >= 2:
                            span_ms = max(ts) - min(ts)
                            span_min = max(1e-9, span_ms / 60_000.0)
                            cnt = len(tr)
                            if span_min >= 0.5 and cnt >= 5:
                                notionals: List[float] = []
                                for x in tr:
                                    with suppress(Exception):
                                        price = float(x.get("price") or 0.0)
                                        amt = float(x.get("amount") or 0.0)
                                        if price > 0 and amt > 0:
                                            n = price * amt
                                            if n >= 1.0:
                                                notionals.append(n)
                                if notionals:
                                    avg_n = sum(notionals) / len(notionals)
                                    tpm = float(cnt) / span_min
                                    usdpm = (avg_n * float(cnt)) / span_min
                                    notionals.sort()
                                    med = notionals[len(notionals) // 2]
                                    if explain:
                                        row.reasons_all.append("fallback:tape_span")

                if tpm == 0.0 and usdpm == 0.0:
                    usdpm = float(row.quote_volume_24h) / 1440.0
                    tpm = 0.0
                    med = med or 0.0
                    if explain:
                        row.reasons_all.append("fallback:24h_rate")

                row.depth_at_bps = depth_map
                row.trades_per_min = tpm
                row.usd_per_min = usdpm
                row.median_trade_usd = med
                row.imbalance = imb

                # --- base enrichments ---
                row.vol_pattern = _compute_vol_stability(candles, is_candles=True, exchange="gate") or _compute_vol_stability(tr)
                row.atr_proxy = _compute_volatility_proxy(candles, is_candles=True, exchange="gate") or _compute_volatility_proxy(tr)
                row.dca_potential = _compute_dca_potential(row, tr or [], candles, "gate")
                d5_min = min(row.depth_at_bps.get(5, {"bid_usd": 0, "ask_usd": 0}).values())
                row.liquidity_grade = _compute_liquidity_grade(d5_min)

                # --- candles_cache enrich (optional) ---
                had_cache_hit = False
                if fetch_candles and hasattr(candles_cache, "get_stats"):
                    try:
                        ret = candles_cache.get_stats(sym, venue="gate", refresh=True)
                        stats = await _with_timeout(ret, 3.0, default=None) if asyncio.iscoroutine(ret) else ret
                    except Exception:
                        stats = None
                    if isinstance(stats, dict):
                        had_cache_hit = True
                        row.atr1m_pct = float(stats.get("atr1m_pct", 0.0))
                        row.spike_count_90m = int(stats.get("spike_count_90m", 0) or 0)
                        row.pullback_median_retrace = float(stats.get("pullback_median_retrace", 0.0))
                        row.grinder_ratio = float(stats.get("grinder_ratio", 0.0))
                        row.range_stable_pct = float(stats.get("range_stable_pct", 0.0))
                        row.vol_pattern = int(stats.get("vol_pattern", row.vol_pattern or 0) or 0)
                        row.dca_potential = int(stats.get("dca_potential", row.dca_potential or 0) or 0)
                        row.bars_1m = int(stats.get("bars_1m", 0))
                        row.last_candle_ts = int(stats.get("last_candle_ts", 0))
                        if explain:
                            row.reasons_all.append("candles_cache:hit")
                    else:
                        if explain:
                            row.reasons_all.append("candles_cache:miss")

                # --- ATR placeholder (only if fetch_candles=True and no data or zero) ---
                if fetch_candles and not had_cache_hit:
                    if not getattr(row, "atr1m_pct", None) or row.atr1m_pct == 0.0:
                        row.atr1m_pct = round(_PRESET.min_atr1m_pct * 0.9, 6)   # uses current preset
                        row.spike_count_90m = row.spike_count_90m or 0
                        row.pullback_median_retrace = row.pullback_median_retrace or 0.35
                        row.grinder_ratio = row.grinder_ratio or 0.30
                        if explain:
                            row.reasons_all.append("candles_placeholder:atr")

                return row

        tasks = [asyncio.create_task(_enrich_one(r)) for r in shortlist]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        # 4) filter & score
        stage2: List[ScanRow] = []
        for res, row in zip(enriched, shortlist):
            if isinstance(res, ScanRow):
                candidate = res
            elif isinstance(res, Exception) or res is None:
                candidate = row
                candidate.depth_at_bps = {}  # omit levels on failure
                candidate.trades_per_min = 0.0
                candidate.usd_per_min = float(candidate.quote_volume_24h) / 1440.0
                candidate.median_trade_usd = 0.0
                if explain:
                    candidate.reasons_all.append("timeout:enrich")
                candidate.reason = "partial"
                candidate.vol_pattern = _compute_vol_stability([], is_candles=False)
                candidate.atr_proxy = _compute_volatility_proxy([], is_candles=False)
                candidate.dca_potential = _compute_dca_potential(candidate, [])
                d5_min = 0.0
                candidate.liquidity_grade = _compute_liquidity_grade(d5_min)
            else:
                candidate = row

            _classify_reason(
                candidate,
                min_depth5_usd=min_depth5_usd,
                min_trades_per_min=min_trades_per_min,
                min_usd_per_min=min_usd_per_min,
                spread_cap_bps=spread_cap_bps,
                explain=explain,
            )

            if liquidity_test and candidate.liquidity_grade == "C":
                candidate.reason = "low liquidity grade"
                if explain:
                    candidate.reasons_all.append("filtered:grade_C")
                continue

            d5 = candidate.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
            d10 = candidate.depth_at_bps.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})

            if min_depth5_usd and min(d5["bid_usd"], d5["ask_usd"]) < min_depth5_usd:
                continue
            if min_depth10_usd and min(d10["bid_usd"], d10["ask_usd"]) < min_depth10_usd:
                continue
            if min_trades_per_min and candidate.trades_per_min < min_trades_per_min:
                continue
            if min_usd_per_min and candidate.usd_per_min < min_usd_per_min:
                continue
            if min_median_trade_usd and candidate.median_trade_usd < min_median_trade_usd:
                continue
            if min_vol_pattern > 0 and (candidate.vol_pattern or 0) < min_vol_pattern:
                continue
            if (candidate.atr_proxy or 0) > max_atr_proxy:
                continue

            sum5 = min(d5["bid_usd"], d5["ask_usd"])
            if activity_ratio > 0:
                actual_ratio = _compute_activity_ratio(candidate.usd_per_min, sum5, default=0.0)
                if actual_ratio < activity_ratio:
                    if explain:
                        candidate.reasons_all.append("filtered:activity_ratio")
                    continue

            candidate.score = _score_row(candidate, depth_key_bps=5)
            stage2.append(candidate)

        stage2.sort(key=lambda x: (-(x.score if x.score is not None else -1e9)))
        out = stage2[:limit]

        if use_cache:
            _CACHE[cache_key] = (now, out)
        return out



# ─────────────────────────── MEXC scanner ───────────────

async def _scan_one_quote_mexc(
    *,
    client: httpx.AsyncClient,
    quote: str,
    limit: int,
    min_quote_vol_usd: float,
    max_spread_bps: float,
    include_stables: bool,
    exclude_leveraged: bool,
    symbols: Optional[List[str]] = None,
) -> List[ScanRow]:
    """
    Stage 1 (MEXC): bookTicker + 24h volumes + max spread.
    Fast-path: if settings.symbols present — prefer those.
    """
    book_all = await _mexc_fetch_book_tickers(client, quote=quote)

    symbols_hint: List[str] = []
    try:
        symbols_hint = [s.upper() for s in (getattr(settings, "symbols", []) or []) if str(s).strip()]
        symbols_hint = [s for s in symbols_hint if s.endswith(quote.upper())]
    except Exception:
        symbols_hint = []

    if symbols:
        target = {s.upper() for s in symbols}
        book = [t for t in book_all if str(t.get("symbol", "")).upper() in target]
        vols24 = await _mexc_fetch_24h(client, list(target))
    else:
        if symbols_hint:
            book = [t for t in book_all if str(t.get("symbol", "")).upper() in symbols_hint]
        else:
            book = book_all[:200]
        symbols_for_vol = [str(x.get("symbol", "")) for x in book if isinstance(x, dict)]
        vols24 = await _mexc_fetch_24h(client, symbols_for_vol)

    rows: List[ScanRow] = []
    for t in book:
        with suppress(Exception):
            sym = str(t.get("symbol", "")).upper()
            if not sym.endswith(quote.upper()):
                continue
            base_ccy = sym[:-len(quote)]
            if exclude_leveraged and _looks_like_leveraged(base_ccy):
                continue
            if not include_stables and _is_stable(base_ccy):
                continue
            bid = float(t.get("bidPrice") or 0.0)
            ask = float(t.get("askPrice") or 0.0)
            if bid <= 0.0 or ask <= 0.0 or ask <= bid:
                continue
            last = float(t.get("lastPrice") or 0.0) if "lastPrice" in t else (bid + ask) * 0.5
            vj = vols24.get(sym, {})
            quote_vol = float(vj.get("quoteVolume", 0.0) or vj.get("quoteVolumeUSDT", 0.0) or 0.0)
            base_vol = float(vj.get("volume", 0.0))
            if quote_vol < float(min_quote_vol_usd):
                continue

            # Use settings overrides if present (future-proof)
            mexc_maker = getattr(settings, "mexc_maker_fee", _MEXC_DEFAULT_MAKER)
            mexc_taker = getattr(settings, "mexc_taker_fee", _MEXC_DEFAULT_TAKER)
            mexc_zero = getattr(settings, "mexc_zero_fee", True)
            
            row = ScanRow(
                symbol=sym,
                exchange="mexc",
                bid=bid,
                ask=ask,
                last=last,
                base_volume_24h=base_vol,
                quote_volume_24h=quote_vol,
                maker_fee=float(mexc_maker),
                taker_fee=float(mexc_taker),
                zero_fee=bool(mexc_zero),
            )
            _apply_stage1_fields_and_effective(row)
            if max_spread_bps > 0.0 and row.spread_bps > max_spread_bps:
                continue
            rows.append(row)

    rows.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
    return rows[: max(limit * 4, 50)]


async def scan_mexc_quote(
    *,
    quote: str = "USDT",
    limit: int = 100,
    min_quote_vol_usd: float = 0.0,
    min_spread_pct: Optional[float] = None,
    max_spread_bps: Optional[float] = 10.0,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    depth_levels_bps: Sequence[int] | None = None,
    min_depth5_usd: float = 0.0,
    min_depth10_usd: float = 0.0,
    min_trades_per_min: float = 0.0,
    min_usd_per_min: float = 0.0,
    min_median_trade_usd: float = 0.0,
    min_vol_pattern: float = 0.0,
    max_atr_proxy: float = float("inf"),
    activity_ratio: float = 0.0,
    explain: bool = False,
    use_cache: bool = True,
    liquidity_test: bool = False,
    symbols: Optional[List[str]] = None,
    fetch_candles: bool = False,  # NEW
) -> List[ScanRow]:
    """
    MEXC scanner (Spot):
      • Stage 1: bookTicker + 24h volumes, MAX spread filter.
      • Stage 2: depth@bps & tape via REST with short timeouts.
      • Optional: candles_cache enrich if fetch_candles=True.
    """
    limit = max(1, min(500, int(limit)))
    q_upper = quote.upper()
    levels = sorted({int(v) for v in (depth_levels_bps or [5, 10]) if int(v) > 0})
    if 5 not in levels:
        levels.append(5)
    if 10 not in levels:
        levels.append(10)
    levels.sort()

    if max_spread_bps is not None:
        spread_cap_bps = float(max_spread_bps)
    elif min_spread_pct is not None:
        spread_cap_bps = float(min_spread_pct) * 100.0
    else:
        spread_cap_bps = 10.0

    cache_key = (
        f"mexc:{q_upper}:{limit}:{min_quote_vol_usd}:{spread_cap_bps}:{include_stables}:{exclude_leveraged}:"
        f"{tuple(levels)}:{min_depth5_usd}:{min_depth10_usd}:{min_trades_per_min}:{min_usd_per_min}:"
        f"{min_median_trade_usd}:{min_vol_pattern}:{max_atr_proxy}:{activity_ratio}:{int(explain)}:{int(liquidity_test)}"
        f":{tuple(sorted(symbols or []))}:{int(fetch_candles)}"
    )
    now = time.monotonic()
    if use_cache and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts <= _CACHE_TTL:
            return data[:limit]

    base_url = _mexc_rest_base()
    headers = {"Accept": "application/json", "User-Agent": "scanner/1.0"}
    hooks = _mk_mexc_hooks(getattr(settings, "http_debug_mexc", False)) or {}

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=_http_timeout(False),
        event_hooks=hooks,
        limits=httpx.Limits(
            max_connections=100,        # ← ADD: Allow 100 total connections
            max_keepalive_connections=50  # ← ADD: Keep 50 alive
        ),
    ) as cli:
        stage1_all = await _scan_one_quote_mexc(
            client=cli,
            quote=q_upper,
            limit=limit,
            min_quote_vol_usd=min_quote_vol_usd,
            max_spread_bps=spread_cap_bps,
            include_stables=include_stables,
            exclude_leveraged=exclude_leveraged,
            symbols=symbols,
        )
        if not stage1_all:
            _CACHE[cache_key] = (now, [])
            return []

        stage1_all.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
        shortlist = stage1_all[: min(len(stage1_all), 100)]

        mexc_concurrency = getattr(settings, "mexc_scan_concurrency", 10)
        sem = asyncio.Semaphore(int(mexc_concurrency))
        log.debug("MEXC scanner: concurrency=%d, explain=%s", mexc_concurrency, explain)

        async def _enrich_one(row: ScanRow) -> Optional[ScanRow]:
            async with sem:
                sym = row.symbol
                mid = (row.bid + row.ask) * 0.5

                ob = await _with_timeout(_mexc_fetch_depth(cli, sym, limit=50), 3.0, default=None)
                bids: List[Tuple[float, float]] = []
                asks: List[Tuple[float, float]] = []
                imb = 0.5
                if isinstance(ob, dict):
                    for it in (ob.get("bids") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0:
                                bids.append((p, qt))
                    bids.sort(key=lambda x: x[0], reverse=True)
                    for it in (ob.get("asks") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0:
                                asks.append((p, qt))
                    asks.sort(key=lambda x: x[0])
                    if bids and asks:
                        mid = (bids[0][0] + asks[0][0]) * 0.5
                        imb = _imbalance_from_sizes(bids[0][1], asks[0][1])
                else:
                    if explain:
                        row.reasons_all.append("timeout:depth")

                depth_map = _build_depth_map(bids, asks, mid, levels)

                tr = await _with_timeout(_mexc_fetch_trades(cli, sym, limit=120), 3.0, default=None)
                candles = await _with_timeout(_mexc_fetch_candles(cli, sym, limit=60), 3.0, default=None)
                if tr is None and explain:
                    row.reasons_all.append("timeout:tape")
                if candles is None and explain:
                    row.reasons_all.append("timeout:candles")

                def _calc_window(minutes: int) -> Tuple[float, float, float]:
                    if not isinstance(tr, list) or not tr:
                        return 0.0, 0.0, 0.0
                    now_ms = int(time.time() * 1000)
                    wins_ms = minutes * 60_000
                    amts: List[float] = []
                    total_usd = 0.0
                    total_cnt = 0
                    for x in tr:
                        with suppress(Exception):
                            ts = int(x.get("time") or x.get("T") or 0)
                            if ts <= 0 or now_ms - ts > wins_ms:
                                continue
                            price = float(x.get("price") or 0.0)
                            qty = float(x.get("qty") or 0.0)
                            if price <= 0 or qty <= 0:
                                continue
                            notional = price * qty
                            if notional < 1.0:
                                continue
                            amts.append(notional)
                            total_usd += notional
                            total_cnt += 1
                    if total_cnt == 0:
                        return 0.0, 0.0, 0.0
                    amts.sort()
                    median = amts[len(amts) // 2]
                    return float(total_cnt) / float(minutes), float(total_usd) / float(minutes), float(median)

                tpm, usdpm, med = _calc_window(1)
                if tpm == 0.0 and usdpm == 0.0:
                    tpm, usdpm, med = _calc_window(5)
                    if (tpm > 0.0 or usdpm > 0.0) and explain:
                        row.reasons_all.append("fallback:tape_5m")

                if (tpm == 0.0 and usdpm == 0.0) and isinstance(tr, list) and tr:
                    with suppress(Exception):
                        ts_list = [int(x.get("time") or x.get("T") or 0) for x in tr if int(x.get("time") or x.get("T") or 0) > 0]
                        if len(ts_list) >= 2:
                            span_ms = max(ts_list) - min(ts_list)
                            span_min = max(1e-9, span_ms / 60_000.0)
                            cnt = len(tr)
                            if span_min >= 0.5 and cnt >= 5:
                                notionals: List[float] = []
                                for x in tr:
                                    with suppress(Exception):
                                        price = float(x.get("price") or 0.0)
                                        qty = float(x.get("qty") or 0.0)
                                        if price > 0 and qty > 0:
                                            n = price * qty
                                            if n >= 1.0:
                                                notionals.append(n)
                                if notionals:
                                    avg_n = sum(notionals) / len(notionals)
                                    tpm = float(cnt) / span_min
                                    usdpm = (avg_n * float(cnt)) / span_min
                                    notionals.sort()
                                    med = notionals[len(notionals) // 2]
                                    if explain:
                                        row.reasons_all.append("fallback:tape_span")

                if tpm == 0.0 and usdpm == 0.0:
                    usdpm = float(row.quote_volume_24h) / 1440.0
                    tpm = 0.0
                    med = med or 0.0
                    if explain:
                        row.reasons_all.append("fallback:24h_rate")

                row.depth_at_bps = depth_map
                row.trades_per_min = tpm
                row.usd_per_min = usdpm
                row.median_trade_usd = med
                row.imbalance = imb

                row.vol_pattern = _compute_vol_stability(candles, is_candles=True, exchange="mexc") or _compute_vol_stability(tr or [])
                row.atr_proxy = _compute_volatility_proxy(candles, is_candles=True, exchange="mexc") or _compute_volatility_proxy(tr or [])
                row.dca_potential = _compute_dca_potential(row, tr or [], candles, "mexc")
                d5_min = min(row.depth_at_bps.get(5, {"bid_usd": 0, "ask_usd": 0}).values())
                row.liquidity_grade = _compute_liquidity_grade(d5_min)

                if fetch_candles and hasattr(candles_cache, "get_stats"):
                    try:
                        ret = candles_cache.get_stats(sym, venue="mexc", refresh=True)
                        if asyncio.iscoroutine(ret):
                            stats = await _with_timeout(ret, 3.0, default=None)
                        else:
                            stats = ret
                    except Exception:
                        stats = None
                    if isinstance(stats, dict):
                        row.atr1m_pct = float(stats.get("atr1m_pct", 0.0))
                        row.spike_count_90m = int(stats.get("spike_count_90m", 0) or 0)
                        row.pullback_median_retrace = float(stats.get("pullback_median_retrace", 0.0))
                        row.grinder_ratio = float(stats.get("grinder_ratio", 0.0))
                        row.range_stable_pct = float(stats.get("range_stable_pct", 0.0))
                        row.vol_pattern = int(stats.get("vol_pattern", row.vol_pattern or 0) or 0)
                        row.dca_potential = int(stats.get("dca_potential", row.dca_potential or 0) or 0)
                        row.bars_1m = int(stats.get("bars_1m", 0))
                        row.last_candle_ts = int(stats.get("last_candle_ts", 0))
                        if explain:
                            row.reasons_all.append("candles_cache:hit")
                    else:
                        if explain:
                            row.reasons_all.append("candles_cache:miss")

                return row

        async def _guarded(task_coro):
            return await _with_timeout(task_coro, 8.0, default=None)

        tasks = [asyncio.create_task(_guarded(_enrich_one(r))) for r in shortlist]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        stage2: List[ScanRow] = []
        for res, row in zip(enriched, shortlist):
            if isinstance(res, ScanRow):
                candidate = res
            elif isinstance(res, Exception) or res is None:
                candidate = row
                candidate.depth_at_bps = {}  # omit levels on failure
                candidate.trades_per_min = 0.0
                candidate.usd_per_min = float(candidate.quote_volume_24h) / 1440.0
                candidate.median_trade_usd = 0.0
                candidate.reasons_all.append("timeout:enrich") if explain else None
                candidate.reason = "partial"
                candidate.vol_pattern = _compute_vol_stability([], is_candles=False)
                candidate.atr_proxy = _compute_volatility_proxy([], is_candles=False)
                candidate.dca_potential = _compute_dca_potential(candidate, [])
                d5_min = 0.0
                candidate.liquidity_grade = _compute_liquidity_grade(d5_min)
            else:
                candidate = row

            _classify_reason(
                candidate,
                min_depth5_usd=min_depth5_usd,
                min_trades_per_min=min_trades_per_min,
                min_usd_per_min=min_usd_per_min,
                spread_cap_bps=spread_cap_bps,
                explain=explain,
            )

            if liquidity_test and candidate.liquidity_grade == "C":
                candidate.reason = "low liquidity grade"
                if explain:
                    candidate.reasons_all.append("filtered:grade_C")
                continue

            d5 = candidate.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
            d10 = candidate.depth_at_bps.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})
            if min_depth5_usd and min(d5["bid_usd"], d5["ask_usd"]) < min_depth5_usd:
                continue
            if min_depth10_usd and min(d10["bid_usd"], d10["ask_usd"]) < min_depth10_usd:
                continue
            if min_trades_per_min and candidate.trades_per_min < min_trades_per_min:
                continue
            if min_usd_per_min and candidate.usd_per_min < min_usd_per_min:
                continue
            if min_median_trade_usd and candidate.median_trade_usd < min_median_trade_usd:
                continue
            if min_vol_pattern > 0 and (candidate.vol_pattern or 0) < min_vol_pattern:
                continue
            if (candidate.atr_proxy or 0) > max_atr_proxy:
                continue

            sum5 = min(d5["bid_usd"], d5["ask_usd"])
            if activity_ratio > 0:
                actual_ratio = _compute_activity_ratio(candidate.usd_per_min, sum5, default=0.0)
                if actual_ratio < activity_ratio:
                    if explain:
                        candidate.reasons_all.append("filtered:activity_ratio")
                    continue

            candidate.score = _score_row(candidate, depth_key_bps=5)
            stage2.append(candidate)

        stage2.sort(key=lambda x: (-(x.score if x.score is not None else -1e9)))
        out = stage2[:limit]
        if use_cache:
            _CACHE[cache_key] = (now, out)
        return out


# ─────────────────────────── Preset-friendly wrappers ────────────

def _filter_kwargs(d: dict | None, allowed: set[str]) -> dict:
    if not d:
        return {}
    return {k: v for k, v in d.items() if k in allowed}

_ALLOWED_SCAN_KW = {
    "quote", "limit", "min_quote_vol_usd", "min_spread_pct", "max_spread_bps",
    "include_stables", "exclude_leveraged", "depth_levels_bps",
    "min_depth5_usd", "min_depth10_usd", "min_trades_per_min",
    "min_usd_per_min", "min_median_trade_usd", "min_vol_pattern",
    "max_atr_proxy", "activity_ratio",
    "liquidity_test", "symbols",
}

async def scan_gate_with_preset(
    *,
    preset: str = "balanced",
    quote: str = "USDT",
    limit: int = 100,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    explain: bool = True,
    use_cache: bool = True,
    symbols: Optional[List[str]] = None,
    fetch_candles: bool = False,  # NEW
    **overrides,
) -> List[ScanRow]:
    base = PRESETS.get(preset, PRESETS.get("balanced", {})).copy()
    merged = {**base, **(overrides or {})}
    merged.pop("explain", None)
    merged.pop("use_cache", None)
    params = _filter_kwargs(merged, _ALLOWED_SCAN_KW)
    return await scan_gate_quote(
        quote=quote,
        limit=limit,
        include_stables=include_stables,
        exclude_leveraged=exclude_leveraged,
        explain=explain,
        use_cache=use_cache,
        symbols=symbols,
        fetch_candles=fetch_candles,
        **params,
    )

async def scan_mexc_with_preset(
    *,
    preset: str = "balanced",
    quote: str = "USDT",
    limit: int = 100,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    explain: bool = True,
    use_cache: bool = True,
    symbols: Optional[List[str]] = None,
    fetch_candles: bool = False,  # NEW
    **overrides,
) -> List[ScanRow]:
    base = PRESETS.get(preset, PRESETS.get("balanced", {})).copy()
    merged = {**base, **(overrides or {})}
    merged.pop("explain", None)
    merged.pop("use_cache", None)
    params = _filter_kwargs(merged, _ALLOWED_SCAN_KW)
    return await scan_mexc_quote(
        quote=quote,
        limit=limit,
        include_stables=include_stables,
        exclude_leveraged=exclude_leveraged,
        explain=explain,
        use_cache=use_cache,
        symbols=symbols,
        fetch_candles=fetch_candles,
        **params,
    )

# ─────────────────────────── cache hitrate hook (for /healthz) ────────────

def get_cache_hitrate() -> Optional[float]:
    try:
        hr = getattr(candles_cache, "hitrate", None)
        if hr is not None:
            return float(hr)
    except Exception:
        pass
    for name in ("get_cache_hitrate", "get_hitrate"):
        try:
            fn = getattr(candles_cache, name, None)
            if callable(fn):
                val = fn()
                if val is not None:
                    return float(val)
        except Exception:
            continue
    try:
        stats = getattr(candles_cache, "stats", None)
        if callable(stats):
            st = stats()
        else:
            st = stats
        if isinstance(st, dict) and ("hitrate" in st):
            return float(st["hitrate"])
    except Exception:
        pass
    return None


__all__ = [
    "ScanRow",
    "scan_gate_quote",
    "scan_mexc_quote",
    "scan_gate_with_preset",
    "scan_mexc_with_preset",
    "MEXCWebSocketClient",
    "GateWebSocketClient",
    "candles_cache",
    "compute_vol_stability",
    "compute_volatility_proxy",
    "get_cache_hitrate",
]
