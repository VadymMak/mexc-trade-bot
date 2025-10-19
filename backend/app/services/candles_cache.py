# app/services/candles_cache.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Tuple, Iterable, Set
from contextlib import suppress
import math

import httpx

from app.config.settings import settings


# ─────────────────────────── helpers ─────────────────────────────

def _is_demo_mode() -> bool:
    """Mirror market_scanner.py logic so env selection is consistent."""
    try:
        m = getattr(settings, "active_mode", None) or getattr(settings, "account_mode", None)
        return str(m).lower() in {"paper", "demo", "test", "testnet"}
    except Exception:
        try:
            return bool(getattr(settings, "is_demo", False))
        except Exception:
            return False


def _gate_rest_base() -> str:
    # ✅ TEMPORARY FIX: Always use production API (public, no auth, much faster than testnet)
    # Production API is stable and doesn't require authentication for public endpoints
    return "https://api.gateio.ws/api/v4"
    
    # Original code (commented out - uncomment when testnet performance improves):
    # if _is_demo_mode():
    #     return getattr(settings, "gate_testnet_rest_base", None) or "https://api.gateio.ws/api/v4"
    # return getattr(settings, "gate_rest_base", None) or "https://api.gateio.ws/api/v4"


def _mexc_rest_base() -> str:
    if _is_demo_mode():
        return getattr(settings, "mexc_testnet_rest_base", None) or "https://api.mexc.com"
    return getattr(settings, "mexc_rest_base", None) or "https://api.mexc.com"


def _coalesce_symbol(raw: str) -> str:
    """Remove separators and uppercase: 'eth/usdt', 'eth_usdt' → 'ETHUSDT'."""
    if not raw:
        return ""
    s = str(raw).strip()
    for sep in (" ", "/", "-", "_"):
        s = s.replace(sep, "")
    return s.upper()


def _to_pair(sym: str, quote: Optional[str] = None) -> str:
    """
    Normalize into 'BASE_QUOTE' with a single underscore.
    Tries settings.quote first (if provided), then a common quote list.
    Accepts: 'BTCUSDT', 'BTC_USDT', 'btc/usdt', etc.
    """
    s = _coalesce_symbol(sym)
    # Compose candidate quotes (settings.quote preferred)
    q_pref = (quote or getattr(settings, "quote", "USDT") or "USDT").upper()
    common_quotes = [q_pref, "USDT", "USDC", "FDUSD", "BUSD", "USD", "BTC", "ETH"]

    for q in common_quotes:
        if s.endswith(q) and len(s) > len(q):
            base = s[: -len(q)]
            return f"{base}_{q}"

    # If original had underscore, honor it as last resort
    if "_" in (sym or ""):
        parts = str(sym).strip().upper().replace("-", "_").replace("/", "_").split("_")
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]}_{parts[1]}"

    # Fallback: return as-is but ensure one underscore before preferred quote if possible
    # Example: 'BTC' → 'BTC_USDT' (using preferred quote)
    if s and q_pref and not s.endswith(q_pref):
        return f"{s}_{q_pref}"
    return f"{s}_{q_pref}" if s and "_" not in s else s


def _split_pair(pair: str) -> Tuple[str, str]:
    """
    Accepts either 'BASE_QUOTE' or concatenated 'BASEQUOTE'. Returns (BASE, QUOTE).
    Prefers settings.quote if it matches the tail.
    """
    if not pair:
        return "", ""

    p = str(pair).upper().strip()
    if "_" in p:
        b, q = p.split("_", 1)
        return b or "", q or ""

    # No underscore → we need to detect the quote suffix
    q_pref = (getattr(settings, "quote", "USDT") or "USDT").upper()
    for q in [q_pref, "USDT", "USDC", "FDUSD", "BUSD", "USD", "BTC", "ETH"]:
        if p.endswith(q) and len(p) > len(q):
            return p[: -len(q)], q
    return p, ""


# ─────────────────────────── domain ──────────────────────────────

@dataclass
class Candle1m:
    ts: int          # epoch sec
    o: float
    h: float
    l: float
    c: float
    v: float


def _true_range(prev_c: float, h: float, l: float) -> float:
    return max(h - l, abs(h - prev_c), abs(l - prev_c))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        if b == 0:
            return default
        return a / b
    except Exception:
        return default


def _log1p_safe(x: float) -> float:
    try:
        return math.log1p(max(0.0, float(x)))
    except Exception:
        return 0.0


# ─────────────────────────── cache ───────────────────────────────

class CandlesCache:
    """
    Lightweight in-memory cache of 1m candles per venue/symbol (Gate/MEXC spot),
    plus feature calculators and a dict-like stats interface.
    Supports venue-specific fetching and computation.
    """

    def __init__(self) -> None:
        self._candles: Dict[str, List[Candle1m]] = {}         # key: "venue:BASE_QUOTE"
        self._ts_fetch: Dict[str, float] = {}                 # monotonic ts of last fetch
        self._stats: Dict[str, Dict[str, float]] = {}         # key: "venue:BASEQUOTE" -> stats
        self.ttl_sec: float = 30.0                            # refresh every 30s

        # hit/miss counters for health/metrics
        self._hits: int = 0
        self._misses: int = 0

        # background helpers
        self._batch_sem: asyncio.Semaphore = asyncio.Semaphore(8)  # max concurrent REST calls
        self._bg_tasks: Set[asyncio.Task] = set()                  # track spawned tasks to avoid GC

    # ── cache metrics surfaces ──
    @property
    def hitrate(self) -> float:
        denom = self._hits + self._misses
        return (self._hits / denom) if denom > 0 else 0.0

    def get_cache_hitrate(self) -> float:
        return self.hitrate

    def get_hitrate(self) -> float:
        return self.hitrate

    def stats(self) -> Dict[str, float]:
        """Lightweight stats dict (read-only)."""
        return {
            "hitrate": self.hitrate,
            "hits": float(self._hits),
            "misses": float(self._misses),
            "keys": float(len(self._candles)),
        }

    # ---- dict-like helpers ----
    def get(self, symbol: str, venue: str = "gate", default=None):
        key = self._norm_key(symbol, venue)
        return self._stats.get(key, default)

    def __contains__(self, symbol: str) -> bool:
        # Note: venue-less contains check defaults to gate
        key = self._norm_key(symbol)
        return key in self._stats

    def get_stats_cached(self, symbol: str, venue: str = "gate") -> Dict[str, float]:
        """Return stats without refreshing network (fast path)."""
        key = self._norm_key(symbol, venue)
        return self._stats.get(key, {})

    # ---- normalization ----
    def _norm_key(self, symbol: str, venue: str = "gate") -> str:
        # external key: "venue:BASEQUOTE" (no underscore)
        base, quote = _split_pair(_to_pair(symbol, getattr(settings, "quote", "USDT")))
        return f"{venue}:{base}{quote}"

    def _norm_pair(self, symbol: str, venue: str = "gate") -> str:
        # internal candles key: "venue:BASE_QUOTE"
        base, quote = _split_pair(_to_pair(symbol, getattr(settings, "quote", "USDT")))
        return f"{venue}:{base}_{quote}"

    # ---- preset-based fallback ATR (lazy, no hard dependency at import time) ----
    @staticmethod
    def _fallback_atr_fraction() -> float:
        """
        Return ~0.9 * min_atr1m_pct from the active preset (defaults to 'balanced').
        This keeps ATR gating reasonably stable while candles are warming up.
        """
        try:
            # try to get preset name from settings; default to 'balanced'
            preset_name = str(getattr(settings, "scanner_preset", "balanced") or "balanced").lower()
            # lazy import to avoid potential circulars at module import time
            from app.scoring.presets import get_preset
            p = get_preset(preset_name)
            # clamp to a sane small floor in case of misconfig
            return max(0.0005, float(p["min_atr1m_pct"]) * 0.9)
        except Exception:
            # sensible default if presets unavailable
            return 0.00135  # ≈ 0.9 * 0.0015 (0.135% of price)

    # ---- REST fetch ----
    async def _fetch_gate_candles_1m(self, pair: str, limit: int = 300) -> List[Candle1m]:
        """
        GET /spot/candlesticks?currency_pair=BTC_USDT&interval=1m&limit=300
        Gate returns list of: [t_sec_str, v_quote_str, close_str, high_str, low_str, open_str]
        We parse into Candle1m(o,h,l,c,v) and convert t to seconds.
        """
        base_url = _gate_rest_base()
        timeout = httpx.Timeout(connect=5.0, read=8.0, write=3.0)
        async with httpx.AsyncClient(base_url=base_url, headers={"Accept": "application/json"}, timeout=timeout) as cli:
            r = await cli.get(
                "/spot/candlesticks",
                params={"currency_pair": pair, "interval": "1m", "limit": max(50, min(300, int(limit)))},
            )
            r.raise_for_status()
            arr = r.json()
            out: List[Candle1m] = []
            if isinstance(arr, list):
                for row in arr:
                    with suppress(Exception):
                        # Gate spec: [t, v, c, h, l, o]
                        t = int(float(row[0]))
                        v = float(row[1]); c = float(row[2])
                        h = float(row[3]); l = float(row[4]); o = float(row[5])
                        out.append(Candle1m(ts=t, o=o, h=h, l=l, c=c, v=v))
            out.sort(key=lambda x: x.ts)  # ascending by time
            return out

    async def _fetch_mexc_klines_1m(self, symbol: str, limit: int = 300) -> List[Candle1m]:
        """
        GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=300
        MEXC returns list of: [otime_ms, o_str, h_str, l_str, c_str, v_base_str, ...]
        """
        base_url = _mexc_rest_base()
        timeout = httpx.Timeout(connect=5.0, read=8.0, write=3.0)
        async with httpx.AsyncClient(base_url=base_url, headers={"Accept": "application/json"}, timeout=timeout) as cli:
            r = await cli.get(
                "/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": "1m", "limit": max(50, min(1000, int(limit)))},
            )
            r.raise_for_status()
            arr = r.json()
            out: List[Candle1m] = []
            if isinstance(arr, list):
                for row in arr:
                    with suppress(Exception):
                        # MEXC spec: [otime_ms, o, h, l, c, v, ...]
                        t_ms = int(row[0])
                        t = t_ms // 1000
                        o = float(row[1]); h = float(row[2])
                        l = float(row[3]); c = float(row[4]); v = float(row[5])
                        out.append(Candle1m(ts=t, o=o, h=h, l=l, c=c, v=v))
            out.sort(key=lambda x: x.ts)  # ascending by time
            return out

    async def get_1m(self, symbol: str, venue: str = "gate", *, quote_hint: Optional[str] = None, limit: int = 300) -> List[Candle1m]:
        """
        symbol: "BTCUSDT" | "BTC_USDT" | "BTC/USDT"
        venue: "gate" | "mexc"
        returns ascending candles (max 300 for Gate, 1000 for MEXC)
        """
        pair_key = self._norm_pair(symbol, venue)
        now = time.monotonic()
        need_fetch = pair_key not in self._candles or (now - self._ts_fetch.get(pair_key, 0.0)) > self.ttl_sec

        # bump hit/miss before the network call
        if need_fetch:
            self._misses += 1
        else:
            self._hits += 1

        if need_fetch:
            try:
                if venue.lower() == "gate":
                    data = await self._fetch_gate_candles_1m(pair_key.split(":", 1)[1], limit=limit)
                elif venue.lower() == "mexc":
                    # For MEXC, pair is "venue:BASE_USDT" but fetch uses BASEUSDT
                    data = await self._fetch_mexc_klines_1m(pair_key.split(":", 1)[1].replace("_", ""), limit=limit)
                else:
                    data = []
                if data:
                    self._candles[pair_key] = data
                    self._ts_fetch[pair_key] = now
            except Exception:
                # keep old if fetch fails
                pass

        return self._candles.get(pair_key, [])

    # ───────────── feature calculators ─────────────

    def calc_atr_pct(self, candles: List[Candle1m], window: int = 20) -> float:
        if len(candles) < window + 1:
            return 0.0
        trs: List[float] = []
        for i in range(-window, 0):
            prev_c = candles[i - 1].c
            c = candles[i]
            trs.append(_true_range(prev_c, c.h, c.l))
        atr = sum(trs) / float(window)
        return _safe_div(atr, candles[-1].c, 0.0)  # FRACTION (e.g., 0.0042)

    def calc_spike_count_90m(self, candles: List[Candle1m]) -> int:
        """
        Count of “wicky” bars in the last 90 bars:
        wick_ratio = (upper_wick + lower_wick) / (high - low), threshold >= 0.40
        """
        take = candles[-90:] if len(candles) >= 90 else candles
        cnt = 0
        for c in take:
            rng = max(c.h - c.l, 1e-12)
            upper = max(0.0, c.h - max(c.o, c.c))
            lower = max(0.0, min(c.o, c.c) - c.l)
            wick_ratio = (upper + lower) / rng
            if wick_ratio >= 0.40:
                cnt += 1
        return cnt

    def calc_grinder_ratio(self, candles: List[Candle1m], atr_abs: Optional[float] = None, window: int = 120) -> float:
        """
        Share of bars that are 'grind': large body vs ATR and tiny wicks.
        - body/ATR > 0.8
        - total wick ratio < 0.2
        """
        if not candles:
            return 0.0
        take = candles[-window:] if len(candles) >= window else candles
        if atr_abs is None:
            atr_abs = max(1e-8, (candles[-1].h - candles[-1].l))
        g = 0
        for c in take:
            rng = max(c.h - c.l, 1e-12)
            body = abs(c.c - c.o)
            upper = max(0.0, c.h - max(c.o, c.c))
            lower = max(0.0, min(c.o, c.c) - c.l)
            wick_ratio = (upper + lower) / rng
            if (body / atr_abs) > 0.8 and wick_ratio < 0.2:
                g += 1
        return g / float(len(take)) if take else 0.0

    def calc_retrace_median(self, candles: List[Candle1m], lookback: int = 180, swing_k: int = 3) -> float:
        """
        Lightweight impulse/retrace heuristic:
        - find local extrema using k-bar swing
        - impulses = move from extrema[i] to extrema[i+1]
        - retrace depth = |correction| / |impulse|
        return median of last <=10 retraces
        """
        if len(candles) < max(lookback, swing_k * 2 + 1):
            return 0.35
        seg = candles[-lookback:]
        highs: List[int] = []
        lows: List[int] = []
        for i in range(swing_k, len(seg) - swing_k):
            c = seg[i]
            if all(c.h >= seg[j].h for j in range(i - swing_k, i + swing_k + 1)):
                highs.append(i)
            if all(c.l <= seg[j].l for j in range(i - swing_k, i + swing_k + 1)):
                lows.append(i)
        ex_idx = sorted(set(highs + lows))
        if len(ex_idx) < 3:
            return 0.35
        retraces: List[float] = []
        for a, b, c_idx in zip(ex_idx[:-2], ex_idx[1:-1], ex_idx[2:]):
            A, B, C = seg[a], seg[b], seg[c_idx]
            impulse = abs(B.c - A.c) or abs((B.h + B.l) / 2 - (A.h + A.l) / 2)
            correction = abs(C.c - B.c)
            if impulse > 1e-9:
                retraces.append(max(0.0, min(1.0, correction / impulse)))
        return float(median(retraces[-10:])) if retraces else 0.35

    def calc_range_stable_pct(self, candles: List[Candle1m], window: int = 60) -> float:
        """std(close) / mean(close) * 100 (percentage std over last window)."""
        if len(candles) < window:
            return 0.0
        take = candles[-window:]
        closes = [c.c for c in take]
        mean_c = sum(closes) / len(closes)
        if mean_c <= 0:
            return 0.0
        std_c = (sum((c - mean_c) ** 2 for c in closes) / len(closes)) ** 0.5
        pct_std = (std_c / mean_c) * 100.0
        return pct_std

    def calc_vol_pattern_from_v(self, candles: List[Candle1m], window: int = 60) -> int:
        """vol_pattern from candle v: std(v)/mean <0.3 → stable (70+ score)."""
        if len(candles) < window:
            return 0
        vols = [c.v for c in candles[-window:]]
        if len(vols) < 5:
            return 0
        mean_v = sum(vols) / len(vols)
        if mean_v <= 0:
            return 0
        std_v = (sum((v - mean_v) ** 2 for v in vols) / len(vols)) ** 0.5
        ratio = std_v / mean_v
        score = max(0, min(100, 100 - (ratio * 100)))  # invert: low ratio → high score
        return int(score) if ratio < 0.3 else int(score * 0.7)  # bonus for stable

    def calc_dca_potential_from_retrace(self, candles: List[Candle1m], retrace_med: float) -> int:
        """dca_pot proxy: low retrace → high potential (e.g., if med <0.3 → 80+)."""
        base_score = max(0, min(100, int(100 - (retrace_med * 100 * 2))))  # 0.5 retrace → 0 score
        pct_std = self.calc_range_stable_pct(candles)
        if pct_std < 0.1:
            base_score += 20
        return max(0, min(100, base_score))

    # ───────────── compute & expose stats ─────────────

    async def compute_metrics_gate(self, symbol: str) -> Dict[str, float]:
        """Pull candles and compute full feature set for Gate tiering."""
        candles = await self.get_1m(symbol, venue="gate", limit=300)
        key = self._norm_key(symbol, "gate")

        if not candles:
            stats = {
                "atr1m_pct": float(self._fallback_atr_fraction()),
                "spike_count_90m": 0,
                "pullback_median_retrace": 0.35,
                "grinder_ratio": 0.30,
                "imbalance_sigma_hits_60m": 0,
                "range_stable_pct": 0.0,
                "vol_pattern": 0,
                "dca_potential": 0,
                "bars_1m": 0,
                "last_candle_ts": 0,
            }
            self._stats[key] = stats
            return stats

        atr_pct = self.calc_atr_pct(candles)                  # FRACTION
        spike_90 = self.calc_spike_count_90m(candles)
        grinder = self.calc_grinder_ratio(candles)
        retr_med = self.calc_retrace_median(candles)
        range_stable = self.calc_range_stable_pct(candles)
        vol_pat = self.calc_vol_pattern_from_v(candles)
        dca_pot = self.calc_dca_potential_from_retrace(candles, retr_med)

        stats = {
            "atr1m_pct": float(atr_pct),                      # FRACTION, not percent
            "spike_count_90m": int(spike_90),
            "pullback_median_retrace": float(retr_med),
            "grinder_ratio": float(grinder),
            "imbalance_sigma_hits_60m": 0,                    # placeholder
            "range_stable_pct": float(range_stable),
            "vol_pattern": float(vol_pat),
            "dca_potential": float(dca_pot),
            "bars_1m": int(len(candles)),
            "last_candle_ts": int(candles[-1].ts),
        }
        self._stats[key] = stats
        return stats

    async def compute_metrics_mexc(self, symbol: str) -> Dict[str, float]:
        """Pull candles and compute full feature set for MEXC tiering."""
        candles = await self.get_1m(symbol, venue="mexc", limit=300)
        key = self._norm_key(symbol, "mexc")

        if not candles:
            stats = {
                "atr1m_pct": float(self._fallback_atr_fraction()),
                "spike_count_90m": 0,
                "pullback_median_retrace": 0.35,
                "grinder_ratio": 0.30,
                "imbalance_sigma_hits_60m": 0,
                "range_stable_pct": 0.0,
                "vol_pattern": 0,
                "dca_potential": 0,
                "bars_1m": 0,
                "last_candle_ts": 0,
            }
            self._stats[key] = stats
            return stats

        atr_pct = self.calc_atr_pct(candles)                  # FRACTION
        spike_90 = self.calc_spike_count_90m(candles)
        grinder = self.calc_grinder_ratio(candles)
        retr_med = self.calc_retrace_median(candles)
        range_stable = self.calc_range_stable_pct(candles)
        vol_pat = self.calc_vol_pattern_from_v(candles)
        dca_pot = self.calc_dca_potential_from_retrace(candles, retr_med)

        stats = {
            "atr1m_pct": float(atr_pct),                      # FRACTION, not percent
            "spike_count_90m": int(spike_90),
            "pullback_median_retrace": float(retr_med),
            "grinder_ratio": float(grinder),
            "imbalance_sigma_hits_60m": 0,
            "range_stable_pct": float(range_stable),
            "vol_pattern": float(vol_pat),
            "dca_potential": float(dca_pot),
            "bars_1m": int(len(candles)),
            "last_candle_ts": int(candles[-1].ts),
        }
        self._stats[key] = stats
        return stats

    async def compute_metrics(self, symbol: str, venue: str = "gate") -> Dict[str, float]:
        """
        Generic dispatcher (compat helper): compute_metrics(symbol, venue)
        Falls back to Gate if venue-specific implementation is missing.
        """
        compute_method = f"compute_metrics_{venue}"
        if hasattr(self, compute_method):
            return await getattr(self, compute_method)(symbol)
        return await self.compute_metrics_gate(symbol)

    async def get_stats(self, symbol: str, venue: str = "gate", refresh: bool = True) -> Dict[str, float]:
        """
        Returns cached stats if available; refreshes/recomputes if TTL expired or refresh=True.
        """
        key = self._norm_key(symbol, venue)
        if not refresh and key in self._stats:
            return self._stats[key]
        compute_method = f"compute_metrics_{venue}"
        if hasattr(self, compute_method):
            compute_func = getattr(self, compute_method)
            return await compute_func(symbol)
        return await self.compute_metrics_gate(symbol)

    async def aget_stats(self, symbol: str, venue: str = "gate", refresh: bool = True) -> Dict[str, float]:
        return await self.get_stats(symbol, venue, refresh=refresh)

    # ───────────── background helpers ─────────────

    async def _compute_guarded(self, symbol: str, venue: str = "gate") -> None:
        try:
            async with self._batch_sem:
                await self.get_stats(symbol, venue=venue, refresh=True)
        except Exception:
            pass

    def touch_symbols(self, symbols: Iterable[str], venue: str = "gate", *, concurrency: int = 8) -> None:
        """
        Fire-and-forget background refresh for provided symbols (with venue).
        Safe to call from routers after you know the shortlist.
        """
        try:
            if concurrency and concurrency > 0:
                self._batch_sem = asyncio.Semaphore(int(concurrency))
        except Exception:
            pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        for sym in symbols:
            t = loop.create_task(self._compute_guarded(sym, venue=venue))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)

    async def batch_compute(self, symbols: Iterable[str], venue: str = "gate", *, concurrency: int = 8) -> None:
        """Awaitable batch refresher (use in services/tests)."""
        self._batch_sem = asyncio.Semaphore(max(1, int(concurrency)))
        tasks = [asyncio.create_task(self._compute_guarded(s, venue=venue)) for s in symbols]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# Expose a ready-to-use singleton for imports elsewhere
candles_cache = CandlesCache()
