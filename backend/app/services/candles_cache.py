# app/services/candles_cache.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Tuple, Any, Iterable, Set
from contextlib import suppress

import httpx

from app.config.settings import settings


# ─────────────────────────── helpers ─────────────────────────────

def _gate_rest_base() -> str:
    try:
        return settings.gate_testnet_rest_base if getattr(settings, "is_demo", False) else settings.gate_rest_base
    except Exception:
        return "https://api.gateio.ws/api/v4"


def _to_pair(sym: str, quote: str = "USDT") -> str:
    s = (sym or "").upper().replace("-", "").replace("/", "")
    q = (quote or "USDT").upper()
    if s.endswith(q):
        return f"{s[:-len(q)]}_{q}"
    if len(s) > len(q):
        return f"{s[:-len(q)]}_{q}"
    return s


def _split_pair(pair: str) -> Tuple[str, str]:
    if "_" in pair:
        b, q = pair.split("_", 1)
        return b.upper(), q.upper()
    p = pair.upper()
    for q in ("USDT", "USDC", "FDUSD", "BUSD", "USD", "BTC", "ETH"):
        if p.endswith(q):
            return p[:-len(q)], q
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


# ─────────────────────────── cache ───────────────────────────────

class CandlesCache:
    """
    Lightweight in-memory cache of 1m candles per symbol (Gate spot),
    plus feature calculators and a dict-like stats interface.
    """

    def __init__(self) -> None:
        self._candles: Dict[str, List[Candle1m]] = {}         # key: "BASE_QUOTE"
        self._ts_fetch: Dict[str, float] = {}                 # monotonic ts of last fetch
        self._stats: Dict[str, Dict[str, float]] = {}         # computed metrics per symbol (key: "BASEQUOTE")
        self.ttl_sec: float = 30.0                            # refresh every 30s

        # background helpers
        self._batch_sem: asyncio.Semaphore = asyncio.Semaphore(8)  # max concurrent REST calls
        self._bg_tasks: Set[asyncio.Task] = set()                  # track spawned tasks to avoid GC

    # ---- dict-like helpers ----
    def get(self, symbol: str, default=None):
        return self._stats.get(self._norm_key(symbol), default)

    def __contains__(self, symbol: str) -> bool:
        return self._norm_key(symbol) in self._stats

    def get_stats_cached(self, symbol: str) -> Dict[str, float]:
        """Return stats without refreshing network (fast path)."""
        return self._stats.get(self._norm_key(symbol), {})

    # ---- normalization ----
    def _norm_key(self, symbol: str, quote_hint: Optional[str] = None) -> str:
        # external key: "BASEQUOTE" (no underscore)
        base, quote = _split_pair(symbol.replace("/", "_")) if "_" in symbol else _split_pair(_to_pair(symbol))
        if not quote:
            quote = (quote_hint or "USDT").upper()
        return f"{base}{quote}"

    def _norm_pair(self, symbol: str, quote_hint: Optional[str] = None) -> str:
        # internal candles key: "BASE_QUOTE"
        base, quote = _split_pair(symbol.replace("/", "_")) if "_" in symbol else _split_pair(_to_pair(symbol))
        if not quote:
            quote = (quote_hint or "USDT").upper()
        return f"{base}_{quote}"

    # ---- REST fetch ----
    async def _fetch_gate_candles_1m(self, pair: str, limit: int = 300) -> List[Candle1m]:
        """
        GET /spot/candlesticks?currency_pair=BTC_USDT&interval=1m&limit=300
        Gate returns list of [t, v, c, h, l, o] with t as string sec.
        """
        base_url = _gate_rest_base()
        async with httpx.AsyncClient(base_url=base_url, headers={"Accept": "application/json"}, timeout=8.0) as cli:
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
                        # Gate spec: [t, v, c, h, l, o] as strings
                        t = int(float(row[0]))
                        v = float(row[1]); c = float(row[2])
                        h = float(row[3]); l = float(row[4]); o = float(row[5])
                        out.append(Candle1m(ts=t, o=o, h=h, l=l, c=c, v=v))
            out.sort(key=lambda x: x.ts)  # ascending by time
            return out

    async def get_1m(self, symbol: str, *, quote_hint: Optional[str] = None, limit: int = 300) -> List[Candle1m]:
        """
        symbol: "BTCUSDT" | "BTC_USDT"
        returns ascending candles (max 300)
        """
        pair = self._norm_pair(symbol, quote_hint)
        now = time.monotonic()
        need_fetch = pair not in self._candles or (now - self._ts_fetch.get(pair, 0.0)) > self.ttl_sec

        if need_fetch:
            try:
                data = await self._fetch_gate_candles_1m(pair, limit=limit)
                if data:
                    self._candles[pair] = data
                    self._ts_fetch[pair] = now
            except Exception:
                # keep old if fetch fails
                pass

        return self._candles.get(pair, [])

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
        return _safe_div(atr, candles[-1].c, 0.0)

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
        return g / float(len(take))

    def calc_retrace_median(self, candles: List[Candle1m], lookback: int = 180, swing_k: int = 3) -> float:
        """
        Very lightweight impulse/retrace heuristic:
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

    # ───────────── compute & expose stats ─────────────

    async def compute_metrics_gate(self, symbol: str) -> Dict[str, float]:
        """
        Pull candles and compute full feature set for the tiering.
        symbol: "BTCUSDT" | "BTC_USDT"
        """
        candles = await self.get_1m(symbol, limit=300)
        key = self._norm_key(symbol)

        if not candles:
            stats = {
                "atr1m_pct": 0.0,
                "spike_count_90m": 0,
                "pullback_median_retrace": 0.35,
                "grinder_ratio": 0.30,
                "imbalance_sigma_hits_60m": 0,
            }
            self._stats[key] = stats
            return stats

        atr_pct = self.calc_atr_pct(candles)
        spike_90 = self.calc_spike_count_90m(candles)
        grinder = self.calc_grinder_ratio(candles)
        retr_med = self.calc_retrace_median(candles)

        stats = {
            "atr1m_pct": float(atr_pct),
            "spike_count_90m": int(spike_90),
            "pullback_median_retrace": float(retr_med),
            "grinder_ratio": float(grinder),
            "imbalance_sigma_hits_60m": 0,  # placeholder (not computed yet)
        }
        self._stats[key] = stats
        return stats

    async def get_stats(self, symbol: str, refresh: bool = True) -> Dict[str, float]:
        """
        Returns cached stats if available; refreshes/recomputes if TTL expired or refresh=True.
        """
        key = self._norm_key(symbol)
        if not refresh and key in self._stats:
            return self._stats[key]
        return await self.compute_metrics_gate(symbol)

    # Optional async alias used by some routers
    async def aget_stats(self, symbol: str, refresh: bool = True) -> Dict[str, float]:
        return await self.get_stats(symbol, refresh=refresh)

    # ───────────── background helpers ─────────────

    async def _compute_guarded(self, symbol: str) -> None:
        try:
            async with self._batch_sem:
                await self.compute_metrics_gate(symbol)
        except Exception:
            # swallow to avoid crashing background tasks
            pass

    def touch_symbols(self, symbols: Iterable[str], *, concurrency: int = 8) -> None:
        """
        Fire-and-forget background refresh for provided symbols.
        Safe to call from routers after you know the shortlist.
        """
        try:
            # adjust runtime concurrency
            if concurrency > 0 and getattr(self._batch_sem, "_value", None) != concurrency:
                self._batch_sem = asyncio.Semaphore(concurrency)
        except Exception:
            pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # called from sync context without a running loop → skip
            return

        for sym in symbols:
            t = loop.create_task(self._compute_guarded(sym))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)

    async def batch_compute(self, symbols: Iterable[str], *, concurrency: int = 8) -> None:
        """Awaitable batch refresher (use in services/tests)."""
        self._batch_sem = asyncio.Semaphore(max(1, int(concurrency)))
        tasks = [asyncio.create_task(self._compute_guarded(s)) for s in symbols]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# Expose a ready-to-use singleton for imports elsewhere
candles_cache = CandlesCache()
