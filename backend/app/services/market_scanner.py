# app/services/market_scanner.py
"""
Market Scanner for Gate.io and MEXC (spot).
Stage 1: 24h stats + spread screening
Stage 2: lightweight depth/tape enrichments via REST with short timeouts
Scoring: volume/depth/spread with a few optional pattern proxies
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field, asdict
from statistics import stdev
from typing import Any, Dict, List, Optional, Tuple, Sequence
from contextlib import suppress

import httpx

from app.config.settings import settings
from app.scoring.presets import PRESETS
from app.services.book_tracker import book_tracker  # noqa: F401

# Import *only* MEXC WS from ws_client; Gate WS is in its canonical module.
from app.market_data.ws_client import MEXCWebSocketClient  # noqa: F401 (used by other modules at runtime)
from app.market_data.gate_ws import GateWebSocketClient    # noqa: F401 (used by other modules at runtime)

# ─────────────────────────── optional candles cache export ───────────────────────────
# Router does a soft import of `candles_cache` from here as a fallback; make sure it exists.
try:
    # If your project provides a real cache, import it:
    from app.services.candles_cache import candles_cache  # type: ignore
except Exception:
    candles_cache: Any = {}  # harmless no-op fallback


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

    # bot-enriched fields
    vol_pattern: Optional[int] = None       # 0-100 match score (e.g., stable_vol)
    net_profit_pct: Optional[float] = None  # effective profit after fees (%)
    liquidity_grade: Optional[str] = None   # 'A'/'B'/'C'
    dca_potential: Optional[int] = None     # 0-100 proxy score
    atr_proxy: Optional[float] = None       # std/ATR-like proxy
    # For live WS updates
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)

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
        # add legacy/short aliases expected by some printers
        d["eff_taker_bps"] = self.eff_spread_taker_bps
        d["eff_maker_bps"] = self.eff_spread_maker_bps
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ScanRow":
        # handle aliases if present
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


# ─────────────────────────── helpers ─────────────────────────────

def _is_demo_mode() -> bool:
    try:
        m = getattr(settings, "active_mode", None) or getattr(settings, "account_mode", None)
        return str(m).lower() in {"paper", "demo", "test", "testnet"}
    except Exception:
        return False


def _gate_rest_base() -> str:
    if _is_demo_mode():
        return getattr(settings, "gate_testnet_rest_base", None) or "https://api-testnet.gateapi.io/api/v4"
    return getattr(settings, "gate_rest_base", None) or "https://api.gateio.ws/api/v4"


def _mexc_rest_base() -> str:
    # Honor demo/testnet base if we're in demo mode and it's provided.
    if _is_demo_mode():
        tb = getattr(settings, "mexc_testnet_rest_base", None)
        if tb:
            return tb
    return getattr(settings, "mexc_rest_base", None) or "https://api.mexc.com"


def _to_pair(sym: str, quote: str = "USDT") -> str:
    s = sym.upper().replace("-", "").replace("/", "")
    q = quote.upper()
    if s.endswith(q):
        return f"{s[:-len(q)]}_{q}"
    if len(s) > len(q):
        return f"{s[:-len(q)]}_{q}"
    return s


def _from_pair(pair: str) -> str:
    return pair.replace("_", "").upper()

# --- public, test-friendly aliases for private helpers ---
def compute_vol_stability(data, *, is_candles: bool = False, exchange: str = "gate") -> int:
    return _compute_vol_stability(data, is_candles=is_candles, exchange=exchange)

def compute_volatility_proxy(data, *, is_candles: bool = False, exchange: str = "gate") -> float:
    return _compute_volatility_proxy(data, is_candles=is_candles, exchange=exchange)


def _split_pair(pair: str) -> Tuple[str, str]:
    if "_" in pair:
        b, q = pair.split("_", 1)
        return b.upper(), q.upper()
    p = pair.upper()
    for q in ("USDT", "USD", "FDUSD", "BUSD", "BTC", "ETH"):
        if p.endswith(q):
            return p[:-len(q)], q
    return p, ""


def _looks_like_leveraged(base: str) -> bool:
    b = base.upper()
    return any(sfx in b for sfx in ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR"))


def _is_stable(ccy: str) -> bool:
    return ccy.upper() in {"USDT", "USDC", "FDUSD", "BUSD", "DAI", "TUSD"}


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


# fees → bps

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
    # Simple net profit proxy: positive if maker spread after fees is positive
    row.net_profit_pct = (eff_m / 100.0 if eff_m is not None else 0.0) + (0.1 if row.zero_fee else 0.0)


def _log1p_safe(x: float) -> float:
    with suppress(Exception):
        return math.log1p(max(0.0, float(x)))
    return 0.0


# ---- unified score weights resolver (accepts SCORE_W_* and score_w_*) ----
def _score_w(key: str, default: float) -> float:
    """
    Resolve a scoring weight.
    Prefers uppercase `SCORE_W_<KEY>` (e.g., SCORE_W_USD_PER_MIN),
    falls back to lowercase `score_w_<key>` (e.g., score_w_usd_per_min),
    then to the provided default.
    """
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
    # Weights can be tuned via ENV or settings (both supported)
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

    # pattern bonuses/penalties
    vol_term = w_vol * (row.vol_pattern or 0) / 100.0
    dca_term = w_dca * (row.dca_potential or 0) / 100.0
    atr_pen = w_atr * _log1p_safe(row.atr_proxy or 0) / 10.0

    # modest spread bonus for tight markets
    spread_bonus = 0.1 * max(0.0, (10.0 - row.spread_bps) / 10.0)

    return usd_term + depth_term + vol_term + dca_term - spread_pen - atr_pen + spread_bonus


def _compute_vol_stability(data: Any, *, is_candles: bool = False, exchange: str = "gate") -> int:
    """
    Returns a 0-100 stability score.
    If candles provided: use base volume series; else from trades qty.
    """
    vols: List[float] = []
    if is_candles:
        if exchange == "gate":
            for c in (data or [])[-20:]:
                with suppress(Exception):
                    # gate candle shape: [t, v_quote, o, h, l, c, v_base]
                    if len(c) > 6:
                        v = float(c[6])
                        if v > 0:
                            vols.append(v)
        else:  # mexc candle: [t, o, h, l, c, v_base, ...]
            for c in (data or [])[-20:]:
                with suppress(Exception):
                    if len(c) > 5:
                        v = float(c[5])
                        if v > 0:
                            vols.append(v)
    else:
        # trades: expect list of dicts or tuples
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
        return 50  # neutral
    mean_v = sum(vols) / n if n else 0
    if mean_v <= 0:
        return 50
    std_v = stdev(vols) if n >= 2 else 0.0
    ratio = std_v / mean_v
    score = 50 + int((1 - min(ratio, 1)) * 50)  # low ratio → higher score
    if ratio < 0.5:
        score += 20
    return min(100, max(0, score))


def _compute_volatility_proxy(data: Any, *, is_candles: bool = False, exchange: str = "gate") -> float:
    """
    ATR-like: from candles (avg true range) or from trades (avg |Δp|).
    """
    if is_candles:
        trs: List[float] = []
        if len(data or []) >= 2:
            for i in range(1, min(20, len(data))):
                with suppress(Exception):
                    if exchange == "gate":
                        prev_c = float(data[i - 1][5])
                        h = float(data[i][3])
                        l = float(data[i][4])
                    else:
                        prev_c = float(data[i - 1][4])
                        h = float(data[i][2])
                        l = float(data[i][3])
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    trs.append(tr)
        if trs:
            return sum(trs) / len(trs)

        closes: List[float] = []
        if exchange == "gate":
            closes = [float(c[5]) for c in (data or [])[-20:] if len(c) > 5 and float(c[5]) > 0]
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
    """Simple proxy: median_trade/usd_min * 100 + bonus for very low std of closes."""
    med = row.median_trade_usd or 0.0
    usdpm = row.usd_per_min or 0.0
    base_score = (med / usdpm * 100) if usdpm > 0 else 0

    closes: List[float] = []
    if candles and len(candles) >= 5:
        if exchange == "gate":
            closes = [float(c[5]) for c in candles[-10:] if len(c) > 5 and float(c[5]) > 0]
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
    """A/B/C from min depth@5bps."""
    if depth_min_usd > 5000:
        return "A"
    if depth_min_usd > 2000:
        return "B"
    return "C"


def _http_timeout(short: bool = False) -> httpx.Timeout:
    # Slightly generous timeouts to avoid flakiness
    read_timeout = 5.0 if short else 10.0
    return httpx.Timeout(connect=5.0, read=read_timeout, write=3.0, pool=2.0)


async def _with_timeout(awaitable, seconds: float = 3.0, default=None):
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except Exception:
        return default


async def _retry(coro, max_retries: int = 3) -> Any:
    """Retry on timeout/connect errors."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro
        except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ConnectError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_exc or Exception("Retry failed")


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
    """
    Sets a short row.reason and fills row.reasons_all (doesn't change filtering).
    Priority:
      1) spread too wide
      2) low depth
      3) low turnover (usd/min)
      4) low tpm
      5) partial (if already set earlier)
      6) ok
    """
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
    return await _retry(fetch()) or []


async def _gate_fetch_order_book(client: httpx.AsyncClient, pair: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    async def fetch():
        r = await client.get("/spot/order_book", params={"currency_pair": pair, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    return await _retry(fetch()) or None


async def _gate_fetch_trades(client: httpx.AsyncClient, pair: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    async def fetch():
        r = await client.get("/spot/trades", params={"currency_pair": pair, "limit": min(200, max(50, limit))})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch()) or None


async def _gate_fetch_candles(client: httpx.AsyncClient, pair: str, interval: str = "1m", limit: int = 60) -> Optional[List[List[Any]]]:
    async def fetch():
        r = await client.get("/spot/candlesticks", params={"currency_pair": pair, "interval": interval, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch()) or None


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
    return await _retry(fetch()) or []


async def _mexc_fetch_24h(client: httpx.AsyncClient, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    res: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return res
    sem = asyncio.Semaphore(8)

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
    return await _retry(fetch()) or None


async def _mexc_fetch_trades(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    async def fetch():
        r = await client.get("/api/v3/trades", params={"symbol": symbol, "limit": min(200, max(50, limit))})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch()) or None


async def _mexc_fetch_candles(client: httpx.AsyncClient, symbol: str, interval: str = "1m", limit: int = 60) -> Optional[List[List[Any]]]:
    async def fetch():
        r = await client.get("/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        if r.status_code != 200:
            return None
        j = r.json()
        return [x for x in j] if isinstance(j, list) else None
    return await _retry(fetch()) or None


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

    # pre-filter candidates
    vol_candidates = [
        t for t in tickers
        if isinstance(t, dict) and str(t.get("currency_pair", "")).upper().endswith(f"_{quote.upper()}")
    ]

    # ---- robust symbols filter (if user provided) ----
    if symbols:
        raw_targets = {s.strip().upper() for s in symbols if s and s.strip()}
        q_up = quote.upper()

        targets_flat: set[str] = set()   # e.g., ETHUSDT
        targets_pairs: set[str] = set()  # e.g., ETH_USDT

        for s in raw_targets:
            s_norm = s.replace("-", "").replace("/", "").upper()
            # if just base supplied (e.g., "ETH"), append quote
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

    # base/leveraged/stable filtering
    vol_candidates = [
        t for t in vol_candidates
        if (include_stables or not _is_stable(_split_pair(str(t.get("currency_pair", "")))[0]))
        and (not exclude_leveraged or not _looks_like_leveraged(_split_pair(str(t.get("currency_pair", "")))[0]))
    ]

    # sort by quote_volume desc and cap to 200
    vol_candidates.sort(key=lambda t: float(t.get("quote_volume", 0)), reverse=True)
    if not symbols:
        vol_candidates = vol_candidates[:200]

    # Optional Gate fees from settings
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
) -> List[ScanRow]:
    """
    Gate scanner: Stage1 (24h + spread) → Stage2 (depth/tape) with short timeouts.
    Supports quote="ALL".
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
        f"gate:{q_upper}:{limit}:{min_quote_vol_usd}:{spread_cap_bps}:{include_stables}:{exclude_leveraged}:"
        f"{tuple(levels)}:{min_depth5_usd}:{min_depth10_usd}:{min_trades_per_min}:{min_usd_per_min}:"
        f"{min_median_trade_usd}:{min_vol_pattern}:{max_atr_proxy}:{activity_ratio}:{int(explain)}:{int(liquidity_test)}"
        f":{tuple(sorted(symbols or []))}"
    )
    now = time.monotonic()
    if use_cache and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts <= _CACHE_TTL:
            return data[:limit]

    base_url = _gate_rest_base()
    headers = {"Accept": "application/json", "User-Agent": "scanner/1.0"}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_http_timeout(False)) as cli:
        quotes_to_scan: List[str] = list(_AVAILABLE_QUOTES) if q_upper == "ALL" else [q_upper]

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

        # Cap shortlist to reduce enrich load
        stage1_all.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
        shortlist = stage1_all[: min(len(stage1_all), 100)]

        sem = asyncio.Semaphore(12)

        async def _enrich_one(row: ScanRow) -> Optional[ScanRow]:
            async with sem:
                sym = row.symbol
                q = next((cand for cand in _AVAILABLE_QUOTES if sym.endswith(cand)), quotes_to_scan[0])
                pair = _to_pair(sym, quote=q)
                mid = (row.bid + row.ask) * 0.5

                ob = await _with_timeout(_gate_fetch_order_book(cli, pair, limit=50), 3.0, default=None)
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

                depth_map: Dict[int, Dict[str, float]] = {}
                if bids and asks and mid > 0:
                    for L in levels:
                        b_usd, a_usd = _absorption_usd_in_band(bids, asks, mid, float(L))
                        depth_map[L] = {"bid_usd": b_usd, "ask_usd": a_usd}
                else:
                    for L in levels:
                        depth_map[L] = {"bid_usd": 0.0, "ask_usd": 0.0}

                tr = await _with_timeout(_gate_fetch_trades(cli, pair, limit=120), 3.0, default=None)
                candles = await _with_timeout(_gate_fetch_candles(cli, pair, limit=60), 3.0, default=None)
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

                # enrichments
                row.vol_pattern = _compute_vol_stability(candles, is_candles=True, exchange="gate") or _compute_vol_stability(tr)
                row.atr_proxy = _compute_volatility_proxy(candles, is_candles=True, exchange="gate") or _compute_volatility_proxy(tr)
                row.dca_potential = _compute_dca_potential(row, tr or [], candles, "gate")
                d5_min = min(row.depth_at_bps.get(5, {"bid_usd": 0, "ask_usd": 0}).values())
                row.liquidity_grade = _compute_liquidity_grade(d5_min)

                return row

        tasks = [asyncio.create_task(_enrich_one(r)) for r in shortlist]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        stage2: List[ScanRow] = []
        for res, row in zip(enriched, shortlist):
            if isinstance(res, ScanRow):
                candidate = res
            elif isinstance(res, Exception) or res is None:
                candidate = row
                candidate.depth_at_bps = {L: {"bid_usd": 0.0, "ask_usd": 0.0} for L in levels}
                candidate.trades_per_min = 0.0
                candidate.usd_per_min = float(candidate.quote_volume_24h) / 1440.0
                candidate.median_trade_usd = 0.0
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
            if activity_ratio > 0 and candidate.usd_per_min < activity_ratio * sum5:
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

            row = ScanRow(
                symbol=sym,
                exchange="mexc",
                bid=bid,
                ask=ask,
                last=last,
                base_volume_24h=base_vol,
                quote_volume_24h=quote_vol,
                maker_fee=_MEXC_DEFAULT_MAKER,
                taker_fee=_MEXC_DEFAULT_TAKER,
                zero_fee=True,
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
) -> List[ScanRow]:
    """
    MEXC scanner (Spot):
      • Stage 1: bookTicker + 24h volumes, MAX spread filter.
      • Stage 2: depth@bps & tape via REST with short timeouts.
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
        f":{tuple(sorted(symbols or []))}"
    )
    now = time.monotonic()
    if use_cache and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts <= _CACHE_TTL:
            return data[:limit]

    base_url = _mexc_rest_base()
    headers = {"Accept": "application/json", "User-Agent": "scanner/1.0"}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_http_timeout(False)) as cli:
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

        sem = asyncio.Semaphore(10)

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

                depth_map: Dict[int, Dict[str, float]] = {}
                if bids and asks and mid > 0:
                    for L in levels:
                        b_usd, a_usd = _absorption_usd_in_band(bids, asks, mid, float(L))
                        depth_map[L] = {"bid_usd": b_usd, "ask_usd": a_usd}
                else:
                    for L in levels:
                        depth_map[L] = {"bid_usd": 0.0, "ask_usd": 0.0}

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

                return row

        async def _guarded(task_coro):
            return await _with_timeout(task_coro, 3.0, default=None)

        tasks = [asyncio.create_task(_guarded(_enrich_one(r))) for r in shortlist]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        stage2: List[ScanRow] = []
        for res, row in zip(enriched, shortlist):
            if isinstance(res, ScanRow):
                candidate = res
            elif isinstance(res, Exception) or res is None:
                candidate = row
                candidate.depth_at_bps = {L: {"bid_usd": 0.0, "ask_usd": 0.0} for L in levels}
                candidate.trades_per_min = 0.0
                candidate.usd_per_min = float(candidate.quote_volume_24h) / 1440.0
                candidate.median_trade_usd = 0.0
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
            if activity_ratio > 0 and candidate.usd_per_min < activity_ratio * sum5:
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
    """
    Keep only parameters supported by the scan_*_quote functions.
    This prevents preset keys like max_slip_bps / fee_bps from causing TypeError.
    NOTE: we intentionally EXCLUDE 'explain' and 'use_cache' from presets/overrides
    to avoid multiple-value collisions with explicit wrapper args.
    """
    if not d:
        return {}
    return {k: v for k, v in d.items() if k in allowed}

# The signatures of scan_gate_quote and scan_mexc_quote are aligned, so allowed sets match.
_ALLOWED_SCAN_KW = {
    "quote", "limit", "min_quote_vol_usd", "min_spread_pct", "max_spread_bps",
    "include_stables", "exclude_leveraged", "depth_levels_bps",
    "min_depth5_usd", "min_depth10_usd", "min_trades_per_min",
    "min_usd_per_min", "min_median_trade_usd", "min_vol_pattern",
    "max_atr_proxy", "activity_ratio",
    # intentionally not including "explain" or "use_cache"
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
    **overrides,  # ← accept CLI overrides like depth_levels_bps, min_usd_per_min, etc.
) -> List[ScanRow]:
    """
    Run Gate scanner using a named PRESET and allow per-call overrides.
    CLI/HTTP overrides win over preset defaults.
    """
    base = PRESETS.get(preset, PRESETS.get("balanced", {})).copy()
    merged = {**base, **(overrides or {})}
    # strip keys that must be provided explicitly by wrapper
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
    **overrides,  # ← accept CLI overrides
) -> List[ScanRow]:
    """
    Run MEXC scanner using a named PRESET and allow per-call overrides.
    CLI/HTTP overrides win over preset defaults.
    """
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
        **params,
    )


__all__ = [
    "ScanRow",
    "scan_gate_quote",
    "scan_mexc_quote",
    "scan_gate_with_preset",
    "scan_mexc_with_preset",
    "MEXCWebSocketClient",
    "GateWebSocketClient",
    "candles_cache",
    # add these:
    "compute_vol_stability",
    "compute_volatility_proxy",
]

