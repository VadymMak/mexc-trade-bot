# app/routers/scanner.py
from __future__ import annotations

import asyncio
import math
import logging
import uuid
from inspect import iscoroutinefunction
from typing import List, Dict, Any, Tuple, Awaitable
from time import time

from fastapi import APIRouter, Query, HTTPException
from app.config.settings import settings

from app.strategy.engine import SYMBOL_BLACKLIST

from app.services.market_scanner import (
    scan_gate_quote,
    scan_mexc_quote,
)

# Defaults with safe fallbacks
try:
    SCAN_TIMEOUT_SEC = int(getattr(settings, "scan_endpoint_timeout_sec", 30))
except Exception:
    SCAN_TIMEOUT_SEC = 30

try:
    CANDLE_TIMEOUT_SEC = int(getattr(settings, "candle_fetch_timeout_sec", 4))
except Exception:
    CANDLE_TIMEOUT_SEC = 4

# Prometheus metrics (soft import, no hard dependency)
try:
    from app.infra import metrics as m  # type: ignore
except Exception:  # pragma: no cover
    m = None  # type: ignore

# попытка взять подготовленные метрики свечей (мягкий фоллбэк)
try:
    from app.services.candles_cache import candles_cache  # основной вариант
except Exception:
    try:
        from app.services.market_scanner import candles_cache  # type: ignore
    except Exception:
        candles_cache = {}  # type: ignore

# schemas (включая явную модель строки для Swagger)
from app.schemas.scanner import (
    ScannerRow,
    Metrics,
    FeatureSnapshot,
    ScannerTopResponse,
    FeeInfo,
)
from app.scoring.tiering import snapshot_from_metrics
from app.scoring.presets import get_preset

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

# logger for this router
log = logging.getLogger("scanner.router")

def filter_blacklisted(rows):
    """Remove blacklisted symbols"""
    if not SYMBOL_BLACKLIST:
        return rows
    filtered = [r for r in rows if getattr(r, 'symbol', None) not in SYMBOL_BLACKLIST]
    if len(filtered) < len(rows):
        removed = [getattr(r, 'symbol', '?') for r in rows if getattr(r, 'symbol', None) in SYMBOL_BLACKLIST]
        log.info("🚫 Filtered blacklisted: %s", removed)
    return filtered


# ────────────────────────────── metrics helpers ──────────────────────────────

def _tick_scan_request() -> None:
    if not m:
        return
    try:
        m.api_scan_requests_total.inc()
    except Exception:
        pass


def _tick_top_request() -> None:
    if not m:
        return
    try:
        m.api_top_requests_total.inc()
    except Exception:
        pass


def _observe_scan_latency(start_ts: float) -> None:
    if not m:
        return
    try:
        dur = max(0.0, time() - start_ts)
        m.api_scan_latency_seconds.observe(dur)
    except Exception:
        pass


def _observe_top_latency(start_ts: float) -> None:
    if not m:
        return
    try:
        dur = max(0.0, time() - start_ts)
        m.api_top_latency_seconds.observe(dur)
    except Exception:
        pass


def _set_candidates(n: int) -> None:
    if not m:
        return
    try:
        m.scanner_candidates.set(float(max(0, int(n))))
    except Exception:
        pass


# ────────────────────────────── debug helpers ──────────────────────────────

def _new_req_id() -> str:
    return uuid.uuid4().hex[:8]


def _row_fee_debug(row) -> str:
    mk = getattr(row, "maker_fee", None)
    tk = getattr(row, "taker_fee", None)
    rz = getattr(row, "reasons_all", None)
    src = "unknown"
    if isinstance(rz, list):
        if any("fees:map_applied" in x for x in rz):
            src = "map"
        elif any("fees:env_applied" in x for x in rz):
            src = "env"
        elif any("fees:none" in x for x in rz):
            src = "none"
    return f"maker_fee={mk!r} taker_fee={tk!r} source={src}"


def _preview_row(row) -> str:
    try:
        return (f"{getattr(row,'symbol', '?')} "
                f"bid={getattr(row,'bid',None)} ask={getattr(row,'ask',None)} "
                f"spr_bps={getattr(row,'spread_bps',None)} "
                f"usdpm={getattr(row,'usd_per_min',None)} "
                f"reasons={getattr(row,'reasons_all',None)}")
    except Exception:
        return "<unprintable row>"


# ────────────────────────────── helpers ──────────────────────────────

def _parse_levels(csv: str | None) -> List[int]:
    raw = (csv or "").split(",")
    levels: List[int] = []
    for tok in raw:
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = int(tok)
            if v > 0:
                levels.append(v)
        except Exception:
            continue
    if not levels:
        levels = [5, 10]
    if 5 not in levels:
        levels.append(5)
    if 10 not in levels:
        levels.append(10)
    
    # Validation: warn if user requested invalid levels
    if csv and not levels:
        log.warning("Invalid depth_bps_levels provided: %s, using defaults [5, 10]", csv)
    
    return sorted(set(levels))


def _parse_symbols(csv: str) -> List[str]:
    """
    Accepts comma/space separated tokens. Returns uppercased forms.
    Service layer is tolerant to ETHUSDT / ETH_USDT / ETH-USDT, etc.
    """
    if not csv:
        return []
    items = []
    for tok in csv.replace(" ", ",").split(","):
        t = tok.strip().upper()
        if t:
            items.append(t)
    # uniquify preserving order
    seen = set()
    out = []
    for s in items:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


async def _fetch_candle_stats(sym: str, venue: str | None = None) -> Dict[str, Any]:
    if not candles_cache:
        return {}
    try:
        if isinstance(candles_cache, dict):
            return dict(candles_cache.get(sym, {}))
        if hasattr(candles_cache, "aget_stats"):
            if venue is not None:
                res = await asyncio.wait_for(candles_cache.aget_stats(sym, venue=venue), timeout=CANDLE_TIMEOUT_SEC)
            else:
                res = await asyncio.wait_for(candles_cache.aget_stats(sym), timeout=CANDLE_TIMEOUT_SEC)
            return dict(res or {})
        if hasattr(candles_cache, "get_stats"):
            func = getattr(candles_cache, "get_stats")
            if iscoroutinefunction(func):
                if venue is not None:
                    res = await asyncio.wait_for(func(sym, venue=venue), timeout=CANDLE_TIMEOUT_SEC)
                else:
                    res = await asyncio.wait_for(func(sym), timeout=CANDLE_TIMEOUT_SEC)
            else:
                loop = asyncio.get_running_loop()
                if venue is not None:
                    res = await loop.run_in_executor(None, lambda: func(sym, venue=venue))
                else:
                    res = await loop.run_in_executor(None, lambda: func(sym))
            return dict(res or {})
        compute_attr = f"compute_metrics_{venue}" if venue else None
        if compute_attr and hasattr(candles_cache, compute_attr):
            res = await asyncio.wait_for(getattr(candles_cache, compute_attr)(sym), timeout=CANDLE_TIMEOUT_SEC)
            return dict(res or {})
        if hasattr(candles_cache, "compute_metrics"):
            res = await asyncio.wait_for(candles_cache.compute_metrics(sym), timeout=CANDLE_TIMEOUT_SEC)
            return dict(res or {})
        if hasattr(candles_cache, "compute_metrics_gate"):
            res = await asyncio.wait_for(candles_cache.compute_metrics_gate(sym), timeout=CANDLE_TIMEOUT_SEC)
            return dict(res or {})
        return {}
    except asyncio.TimeoutError:
        return {}
    except Exception:
        return {}
    
def _build_depth_response(depth_map: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    """
    Build depth response with both legacy fields and full map.
    Returns dict with depth5/depth10 fields + depth_at_bps.
    """
    result: Dict[str, Any] = {}
    
    # Extract standard levels
    d5 = depth_map.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    d10 = depth_map.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})
    
    # Legacy fields (always present)
    result["depth5_bid_usd"] = float(d5.get("bid_usd", 0.0))
    result["depth5_ask_usd"] = float(d5.get("ask_usd", 0.0))
    result["depth10_bid_usd"] = float(d10.get("bid_usd", 0.0))
    result["depth10_ask_usd"] = float(d10.get("ask_usd", 0.0))
    
    # Dynamic levels (beyond 5 and 10)
    for level, values in depth_map.items():
        if level in (5, 10):
            continue
        result[f"depth{level}_bid_usd"] = float(values.get("bid_usd", 0.0))
        result[f"depth{level}_ask_usd"] = float(values.get("ask_usd", 0.0))
    
    # Full map
    result["depth_at_bps"] = depth_map
    
    return result


def _pick_eff_spreads(r) -> Tuple[float | None, float | None]:
    eff_bps_taker = getattr(r, "eff_spread_bps_taker", None)
    eff_bps_maker = getattr(r, "eff_spread_bps_maker", None)
    if eff_bps_taker is None:
        eff_bps_taker = getattr(r, "eff_spread_taker_bps", None)
    if eff_bps_maker is None:
        eff_bps_maker = getattr(r, "eff_spread_maker_bps", None)
    return eff_bps_taker, eff_bps_maker


def _row_to_payload(
    r,
    *,
    exchange: str,
    explain: bool,
    rotation: bool = False,
    candle_stats: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    mid = (float(getattr(r, "bid", 0.0) or 0.0) + float(getattr(r, "ask", 0.0) or 0.0)) * 0.5
    if mid <= 0:
        mid = float(getattr(r, "last", 0.0) or 0.0)

    # Effective spreads
    eff_bps_taker, eff_bps_maker = _pick_eff_spreads(r)
    if eff_bps_taker is None or eff_bps_maker is None:
        maker_fee = float(getattr(r, "maker_fee", 0.0) or 0.0)
        taker_fee = float(getattr(r, "taker_fee", 0.0) or 0.0)
        spread_bps = float(getattr(r, "spread_bps", 0.0) or 0.0)
        maker_bps = maker_fee * 1e4
        taker_bps = taker_fee * 1e4
        if eff_bps_taker is None:
            eff_bps_taker = spread_bps + 2.0 * taker_bps
        if eff_bps_maker is None:
            eff_bps_maker = max(spread_bps - 2.0 * maker_bps, 0.0)

    eff_pct_taker = float(eff_bps_taker or 0.0) / 100.0
    eff_pct_maker = float(eff_bps_maker or 0.0) / 100.0

    eff_abs_taker = (mid * float(eff_bps_taker or 0.0) / 1e4) if mid > 0 else 0.0
    eff_abs_maker = (mid * float(eff_bps_maker or 0.0) / 1e4) if mid > 0 else 0.0

    # score
    score = getattr(r, "score", None)
    if score is None:
        dmap = getattr(r, "depth_at_bps", {}) or {}
        d5obj = dmap.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5 = float(min(d5obj.get("bid_usd", 0.0), d5obj.get("ask_usd", 0.0)))
        upm = float(getattr(r, "usd_per_min", 0.0) or 0.0)
        upm_term = math.log10(upm + 10.0)
        depth_term = math.log10(depth5 + 10.0)
        spread_penalty = float(eff_bps_taker or 0.0)
        score = 2.0 * upm_term + 1.5 * depth_term - 0.05 * spread_penalty
        if candle_stats:
            atr_pct = float(candle_stats.get("atr1m_pct", 0.0) or 0.0)
            atr_term = math.log10(max(atr_pct * 100 + 1.0, 1.0)) if atr_pct > 0.005 else -0.5 * (1 - atr_pct * 200)
            score += 0.8 * atr_term

    base: Dict[str, Any] = {
        "exchange": exchange,
        "symbol": getattr(r, "symbol"),
        "bid": getattr(r, "bid"),
        "ask": getattr(r, "ask"),
        "last": getattr(r, "last"),
        "spread_abs": getattr(r, "spread_abs"),
        "spread_pct": getattr(r, "spread_pct"),
        "spread_bps": getattr(r, "spread_bps"),
        "base_volume_24h": getattr(r, "base_volume_24h"),
        "quote_volume_24h": getattr(r, "quote_volume_24h"),
        "trades_per_min": getattr(r, "trades_per_min"),
        "usd_per_min": getattr(r, "usd_per_min"),
        "median_trade_usd": getattr(r, "median_trade_usd"),
        "imbalance": getattr(r, "imbalance"),
        "ws_lag_ms": getattr(r, "ws_lag_ms", None),
        "maker_fee": getattr(r, "maker_fee", None),
        "taker_fee": getattr(r, "taker_fee", None),
        "zero_fee": getattr(r, "zero_fee", None),
        "eff_spread_bps": eff_bps_taker,
        "eff_spread_pct": eff_pct_taker,
        "eff_spread_abs": eff_abs_taker,
        "eff_spread_bps_taker": eff_bps_taker,
        "eff_spread_pct_taker": eff_pct_taker,
        "eff_spread_abs_taker": eff_abs_taker,
        "eff_spread_bps_maker": eff_bps_maker,
        "eff_spread_pct_maker": eff_pct_maker,
        "eff_spread_abs_maker": eff_abs_maker,
        "score": score,
    }

    # Candle metrics
    if candle_stats:
        base.update({
            "atr1m_pct": candle_stats.get("atr1m_pct"),
            "spike_count_90m": candle_stats.get("spike_count_90m"),
            "pullback_median_retrace": candle_stats.get("pullback_median_retrace"),
            "grinder_ratio": candle_stats.get("grinder_ratio"),
            "imbalance_sigma_hits_60m": candle_stats.get("imbalance_sigma_hits_60m"),
        })

    # depth map + legacy fields
    # Depth map + legacy fields
    depth_map = getattr(r, "depth_at_bps", {}) or {}
    
    # Fallback: build from legacy fields if map is empty
    if not depth_map:
        d5b = getattr(r, "depth5_bid_usd", None)
        d5a = getattr(r, "depth5_ask_usd", None)
        d10b = getattr(r, "depth10_bid_usd", None)
        d10a = getattr(r, "depth10_ask_usd", None)
        derived: Dict[int, Dict[str, float]] = {}
        if d5b is not None or d5a is not None:
            derived[5] = {"bid_usd": float(d5b or 0.0), "ask_usd": float(d5a or 0.0)}
        if d10b is not None or d10a is not None:
            derived[10] = {"bid_usd": float(d10b or 0.0), "ask_usd": float(d10a or 0.0)}
        depth_map = derived
    
    # Build depth response with helper
    base.update(_build_depth_response(depth_map))

    if explain:
        base["reason"] = getattr(r, "reason", None) or "ok"
        reasons_all = getattr(r, "reasons_all", None)
        if isinstance(reasons_all, list):
            base["reasons_all"] = reasons_all

    return base


def _fees_from_row(row) -> FeeInfo:
    return FeeInfo(
        maker=getattr(row, "maker_fee", None),
        taker=getattr(row, "taker_fee", None),
        zero_maker=bool(getattr(row, "zero_fee", False)),
    )


async def _run_with_timeout(coro, *, timeout: float, name: str, req_id: str):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        log.warning("[%s] %s scan timed out after %.2fs", req_id, name, timeout)
        raise HTTPException(status_code=504, detail=f"{name} scan timed out after {timeout}s") from e
    except Exception as e:
        log.exception("[%s] %s scan failed: %s", req_id, name, e)
        raise HTTPException(status_code=502, detail=f"{name} scan failed: {str(e)}") from e


def _apply_rotation_queue(payloads: List[Dict[str, Any]], *, top_n: int = 3) -> None:
    if not payloads:
        return
    ranked = sorted(payloads, key=lambda x: (x.get("score") or 0.0), reverse=True)[:top_n]
    queue = [p["symbol"] for p in ranked]
    for p in payloads:
        p["queue"] = queue


# ───────────── Gate: raw list ─────────────

@router.get("/gate/top", response_model=List[ScannerRow])
async def gate_top(
    quote: str = Query("USDT", description='Котируемая валюта: "USDT" | "USDC" | "FDUSD" | "BUSD" | "ALL"'),
    limit: int = Query(100, ge=1, le=500, description="Макс. число результатов"),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    max_spread_bps: float | None = Query(None, ge=0.0, description="Явный потолок спреда в bps (перекрывает min_spread_pct)"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0, description="Мин. 24h объём по котируемой валюте"),
    depth_bps_levels: str = Query("5,10", description='CSV уровней глубины в bps, напр. "3,5,10"'),
    min_depth5_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±5 bps"),
    min_depth10_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±10 bps"),
    min_trades_per_min: float = Query(0.0, ge=0.0, description="Мин. число сделок/мин (окно ~60s)"),
    min_usd_per_min: float = Query(0.0, ge=0.0, description="Мин. оборот USD/мин (окно ~60s)"),
    min_median_trade_usd: float = Query(0.0, ge=0.0, description="Мин. медианный размер сделки, USD"),
    min_vol_pattern: float = Query(0.0, ge=0.0, description="Мин. оценка стабильности объёма (0-100)"),
    max_atr_proxy: float = Query(float("inf"), ge=0.0, description="Макс. ATR proxy"),
    activity_ratio: float = Query(0.0, ge=0.0, description="Мин. отношение USD/мин к depth5_min_side"),
    symbols: str = Query("", description='CSV списка нужных символов (ETHUSDT, BTC_USDT, ...). Пусто = все.'),
    include_stables: bool = Query(False, description="Включать пары с базой-стейблом"),
    exclude_leveraged: bool = Query(True, description="Исключать 3L/3S/UP/DOWN токены"),
    explain: bool = Query(False, description="Добавить краткую причину прохождения/отсевов"),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
    debug: bool = Query(False, description="Log verbose debug info for this request (server logs only)"),
):
    req_id = _new_req_id()
    t0 = time()
    _tick_scan_request()

    quote_in = quote
    quote = (quote or "USDT").upper()
    if quote not in {"USDT", "USDC", "FDUSD", "BUSD", "ALL"}:
        quote = "USDT"
    limit = max(1, min(500, int(limit)))
    levels = _parse_levels(depth_bps_levels)
    symbols_list = _parse_symbols(symbols)

    if debug:
        log.info(
            "[%s] /gate/top start quote=%s(raw=%s) limit=%s levels=%s "
            "min_spread_pct=%s max_spread_bps=%s min_qv=%s min_d5=%s min_d10=%s "
            "min_tpm=%s min_upm=%s min_med=%s min_vol=%s max_atr=%s ar=%s symbols=%s "
            "include_stables=%s exclude_leveraged=%s explain=%s fetch_candles=%s",
            req_id, quote, quote_in, limit, levels,
            min_spread_pct, max_spread_bps, min_quote_vol_usd, min_depth5_usd, min_depth10_usd,
            min_trades_per_min, min_usd_per_min, min_median_trade_usd, min_vol_pattern, max_atr_proxy, activity_ratio, symbols_list,
            include_stables, exclude_leveraged, explain, fetch_candles
        )

    rows = await _run_with_timeout(
        scan_gate_quote(
            quote=quote,
            limit=limit,
            min_quote_vol_usd=min_quote_vol_usd,
            min_spread_pct=min_spread_pct,
            max_spread_bps=max_spread_bps,
            include_stables=include_stables,
            exclude_leveraged=exclude_leveraged,
            depth_levels_bps=levels,
            min_depth5_usd=min_depth5_usd,
            min_depth10_usd=min_depth10_usd,
            min_trades_per_min=min_trades_per_min,
            min_usd_per_min=min_usd_per_min,
            min_median_trade_usd=min_median_trade_usd,
            min_vol_pattern=min_vol_pattern,
            max_atr_proxy=max_atr_proxy,
            activity_ratio=activity_ratio,
            explain=explain,
            use_cache=True,
            liquidity_test=liquidity_test,
            symbols=symbols_list or None,
            fetch_candles=fetch_candles,  # pass-through to service
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="gate",
        req_id=req_id,
    )

    processed_rows = []
    for r in rows:
        if isinstance(r, Exception):
            if explain:
                stub_row = type('StubRow', (), {
                    "symbol": "**error**",
                    "bid": 0.0, "ask": 0.0, "last": 0.0,
                    "spread_abs": 0.0, "spread_pct": 0.0, "spread_bps": 0.0,
                    "base_volume_24h": 0.0, "quote_volume_24h": 0.0,
                    "trades_per_min": 0.0, "usd_per_min": 0.0, "median_trade_usd": 0.0,
                    "imbalance": 0.5, "ws_lag_ms": None,
                    "maker_fee": None, "taker_fee": None, "zero_fee": None,
                    "depth_at_bps": {},
                    "reason": str(r),
                    "reasons_all": [f"scan_error:{type(r).__name__}"],
                })()
                processed_rows.append(stub_row)
        else:
            processed_rows.append(r)
    rows = processed_rows

    if debug:
        log.info("[%s] gate rows=%d", req_id, len(rows))
        for r in rows[:5]:
            log.info("[%s] row %s | %s", req_id, _preview_row(r), _row_fee_debug(r))

    # негBlocking тёплый прогрев кэша свечей
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, venue="gate", concurrency=6))  # type: ignore[misc]
            else:
                func(syms, venue="gate", concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    # Optional candle fetch (extra UI richness)
    candle_stats_map: Dict[str, Dict[str, Any]] = {}
    if fetch_candles:
        tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="gate")) for r in rows]
        fetched = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        candle_stats_map = {
            r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)
        }

    payloads = [
        _row_to_payload(r, exchange="gate", explain=explain, rotation=rotation, candle_stats=candle_stats_map.get(r.symbol))
        for r in rows
    ]
    if rotation:
        _apply_rotation_queue(payloads)

    _observe_scan_latency(t0)
    _set_candidates(len(payloads))

    if debug:
        dur = time() - t0
        log.info("[%s] /gate/top done in %.3fs payloads=%d", req_id, dur, len(payloads))

    return payloads


# ───────────── MEXC: raw list ─────────────

@router.get("/mexc/top", response_model=List[ScannerRow])
async def mexc_top(
    quote: str = Query("USDT", description='Котируемая валюта: например "USDT"'),
    limit: int = Query(100, ge=1, le=500, description="Макс. число результатов"),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    max_spread_bps: float | None = Query(None, ge=0.0, description="Явный потолок спреда в bps (перекрывает min_spread_pct)"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0, description="Мин. 24h объём (quote)"),
    depth_bps_levels: str = Query("5,10", description="CSV уровней глубины в bps"),
    min_depth5_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±5 bps"),
    min_depth10_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±10 bps"),
    min_trades_per_min: float = Query(0.0, ge=0.0, description="Мин. число сделок/мин (окно ~60s)"),
    min_usd_per_min: float = Query(0.0, ge=0.0, description="Мин. оборот USD/мин (окно ~60s)"),
    min_median_trade_usd: float = Query(0.0, ge=0.0, description="Мин. медианный размер сделки, USD"),
    min_vol_pattern: float = Query(0.0, ge=0.0, description="Мин. оценка стабильности объёма (0-100)"),
    max_atr_proxy: float = Query(float("inf"), ge=0.0, description="Макс. ATR proxy"),
    activity_ratio: float = Query(0.0, ge=0.0, description="Мин. отношение USD/мин к depth5_min_side"),
    symbols: str = Query("", description='CSV списка нужных символов (ETHUSDT, BTC_USDT, ...)'),
    include_stables: bool = Query(False, description="Включать пары со стейбл-базой"),
    exclude_leveraged: bool = Query(True, description="Исключать 3L/3S/UP/DOWN токены"),
    explain: bool = Query(False, description="Добавить причины фоллбеков/отсевов"),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
    debug: bool = Query(False, description="Log verbose debug info for this request (server logs only)"),
):
    req_id = _new_req_id()
    t0 = time()
    _tick_scan_request()

    quote = (quote or "USDT").upper()
    limit = max(1, min(500, int(limit)))
    levels = _parse_levels(depth_bps_levels)
    symbols_list = _parse_symbols(symbols)

    if debug:
        log.info(
            "[%s] /mexc/top start quote=%s limit=%s levels=%s include_stables=%s exclude_leveraged=%s "
            "min_spread_pct=%s max_spread_bps=%s min_qv=%s min_d5=%s min_d10=%s "
            "min_tpm=%s min_upm=%s min_med=%s min_vol=%s max_atr=%s ar=%s symbols=%s "
            "explain=%s fetch_candles=%s",
            req_id, quote, limit, levels, include_stables, exclude_leveraged,
            min_spread_pct, max_spread_bps, min_quote_vol_usd, min_depth5_usd, min_depth10_usd,
            min_trades_per_min, min_usd_per_min, min_median_trade_usd, min_vol_pattern, max_atr_proxy, activity_ratio, symbols_list,
            explain, fetch_candles
        )

    rows = await _run_with_timeout(
        scan_mexc_quote(
            quote=quote,
            limit=limit,
            min_quote_vol_usd=min_quote_vol_usd,
            min_spread_pct=min_spread_pct,
            max_spread_bps=max_spread_bps,
            depth_levels_bps=levels,
            min_depth5_usd=min_depth5_usd,
            min_depth10_usd=min_depth10_usd,
            min_trades_per_min=min_trades_per_min,
            min_usd_per_min=min_usd_per_min,
            min_median_trade_usd=min_median_trade_usd,
            min_vol_pattern=min_vol_pattern,
            max_atr_proxy=max_atr_proxy,
            activity_ratio=activity_ratio,
            include_stables=include_stables,
            exclude_leveraged=exclude_leveraged,
            explain=explain,
            use_cache=True,
            liquidity_test=liquidity_test,
            symbols=symbols_list or None,
            fetch_candles=fetch_candles,  # pass-through to service
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="mexc",
        req_id=req_id,
    )

    processed_rows = []
    for r in rows:
        if isinstance(r, Exception):
            if explain:
                stub_row = type('StubRow', (), {
                    "symbol": "**error**",
                    "bid": 0.0, "ask": 0.0, "last": 0.0,
                    "spread_abs": 0.0, "spread_pct": 0.0, "spread_bps": 0.0,
                    "base_volume_24h": 0.0, "quote_volume_24h": 0.0,
                    "trades_per_min": 0.0, "usd_per_min": 0.0, "median_trade_usd": 0.0,
                    "imbalance": 0.5, "ws_lag_ms": None,
                    "maker_fee": None, "taker_fee": None, "zero_fee": None,
                    "depth_at_bps": {},
                    "reason": str(r),
                    "reasons_all": [f"scan_error:{type(r).__name__}"],
                })()
                processed_rows.append(stub_row)
        else:
            processed_rows.append(r)
    rows = processed_rows

    if debug:
        log.info("[%s] mexc rows=%d", req_id, len(rows))
        for r in rows[:5]:
            log.info("[%s] row %s | %s", req_id, _preview_row(r), _row_fee_debug(r))

    candle_stats_map = {}
    if fetch_candles:
        tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="mexc")) for r in rows]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        candle_stats_map = {r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)}

    # тёплый прогрев кэша свечей
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, venue="mexc", concurrency=6))  # type: ignore[misc]
            else:
                func(syms, venue="mexc", concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    payloads = [
        _row_to_payload(r, exchange="mexc", explain=explain, rotation=rotation, candle_stats=candle_stats_map.get(r.symbol))
        for r in rows
    ]
    if rotation:
        _apply_rotation_queue(payloads)

    _observe_scan_latency(t0)
    _set_candidates(len(payloads))

    if debug:
        dur = time() - t0
        log.info("[%s] /mexc/top done in %.3fs payloads=%d", req_id, dur, len(payloads))

    return payloads


# ───────────── Универсальный raw list ─────────────

@router.get("/top", response_model=List[ScannerRow])
async def top_any(
    exchange: str = Query("gate", description='Биржа: "gate" | "mexc" | "all"'),
    quote: str = Query("USDT", description='Котируемая валюта (для "all" действует на обе)'),
    limit: int = Query(100, ge=1, le=500),
    min_spread_pct: float = Query(0.10, ge=0.0),
    max_spread_bps: float | None = Query(None, ge=0.0, description="Явный потолок спреда в bps (перекрывает min_spread_pct)"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0),
    depth_bps_levels: str = Query("5,10"),
    min_depth5_usd: float = Query(0.0, ge=0.0),
    min_depth10_usd: float = Query(0.0, ge=0.0),
    min_trades_per_min: float = Query(0.0, ge=0.0),
    min_usd_per_min: float = Query(0.0, ge=0.0),
    min_median_trade_usd: float = Query(0.0, ge=0.0),
    min_vol_pattern: float = Query(0.0, ge=0.0),
    max_atr_proxy: float = Query(float("inf"), ge=0.0),
    activity_ratio: float = Query(0.0, ge=0.0),
    symbols: str = Query("", description='CSV списка нужных символов (ETHUSDT, BTC_USDT, ...)'),
    include_stables: bool = Query(False),
    exclude_leveraged: bool = Query(True),
    explain: bool = Query(False),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
    debug: bool = Query(False, description="Log verbose debug info for this request (server logs only)"),
):
    req_id = _new_req_id()
    t0 = time()
    _tick_scan_request()

    exch_in = exchange
    exch = (exchange or "gate").lower()
    if exch not in {"gate", "mexc", "all"}:
        exch = "gate"
    quote = (quote or "USDT").upper()
    levels = _parse_levels(depth_bps_levels)
    limit = max(1, min(500, int(limit)))
    symbols_list = _parse_symbols(symbols)

    if debug:
        log.info(
            "[%s] /top start exchange=%s(raw=%s) quote=%s limit=%s levels=%s explain=%s fetch_candles=%s symbols=%s",
            req_id, exch, exch_in, quote, limit, levels, explain, fetch_candles, symbols_list
        )

    tasks: List[Tuple[str, Awaitable[Any]]] = []
    if exch in {"gate", "all"}:
        tasks.append((
            "gate",
            asyncio.wait_for(
                scan_gate_quote(
                    quote=quote,
                    limit=limit,
                    min_quote_vol_usd=min_quote_vol_usd,
                    min_spread_pct=min_spread_pct,
                    max_spread_bps=max_spread_bps,
                    depth_levels_bps=levels,
                    min_depth5_usd=min_depth5_usd,
                    min_depth10_usd=min_depth10_usd,
                    min_trades_per_min=min_trades_per_min,
                    min_usd_per_min=min_usd_per_min,
                    min_median_trade_usd=min_median_trade_usd,
                    min_vol_pattern=min_vol_pattern,
                    max_atr_proxy=max_atr_proxy,
                    activity_ratio=activity_ratio,
                    include_stables=include_stables,
                    exclude_leveraged=exclude_leveraged,
                    explain=explain,
                    use_cache=True,
                    liquidity_test=liquidity_test,
                    symbols=symbols_list or None,
                    fetch_candles=fetch_candles,
                ),
                timeout=SCAN_TIMEOUT_SEC,
            )
        ))
    if exch in {"mexc", "all"}:
        tasks.append((
            "mexc",
            asyncio.wait_for(
                scan_mexc_quote(
                    quote=quote,
                    limit=limit,
                    min_quote_vol_usd=min_quote_vol_usd,
                    min_spread_pct=min_spread_pct,
                    max_spread_bps=max_spread_bps,
                    depth_levels_bps=levels,
                    min_depth5_usd=min_depth5_usd,
                    min_depth10_usd=min_depth10_usd,
                    min_trades_per_min=min_trades_per_min,
                    min_usd_per_min=min_usd_per_min,
                    min_median_trade_usd=min_median_trade_usd,
                    min_vol_pattern=min_vol_pattern,
                    max_atr_proxy=max_atr_proxy,
                    activity_ratio=activity_ratio,
                    include_stables=include_stables,
                    exclude_leveraged=exclude_leveraged,
                    explain=explain,
                    use_cache=True,
                    liquidity_test=liquidity_test,
                    symbols=symbols_list or None,
                    fetch_candles=fetch_candles,
                ),
                timeout=SCAN_TIMEOUT_SEC,
            )
        ))

    names = [name for name, _ in tasks]
    awaits = [aw for _, aw in tasks]
    results = await asyncio.gather(*awaits, return_exceptions=True)

    all_rows: List[Tuple[str, Any]] = []
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            if explain:
                stub_row = type('StubRow', (), {
                    "symbol": "**error**",
                    "bid": 0.0, "ask": 0.0, "last": 0.0,
                    "spread_abs": 0.0, "spread_pct": 0.0, "spread_bps": 0.0,
                    "base_volume_24h": 0.0, "quote_volume_24h": 0.0,
                    "trades_per_min": 0.0, "usd_per_min": 0.0, "median_trade_usd": 0.0,
                    "imbalance": 0.5, "ws_lag_ms": None,
                    "maker_fee": None, "taker_fee": None, "zero_fee": None,
                    "depth_at_bps": {},
                    "reason": str(res),
                    "reasons_all": [f"route_error:{type(res).__name__}"],
                })()
                all_rows.append((name, [stub_row]))
            else:
                all_rows.append((name, []))
            if debug:
                log.warning("[%s] %s branch raised %s", req_id, name, type(res).__name__)
            continue

        processed = []
        for item in res:
            if isinstance(item, Exception):
                if explain:
                    stub_row = type('StubRow', (), {
                        "symbol": "**error**",
                        "bid": 0.0, "ask": 0.0, "last": 0.0,
                        "spread_abs": 0.0, "spread_pct": 0.0, "spread_bps": 0.0,
                        "base_volume_24h": 0.0, "quote_volume_24h": 0.0,
                        "trades_per_min": 0.0, "usd_per_min": 0.0, "median_trade_usd": 0.0,
                        "imbalance": 0.5, "ws_lag_ms": None,
                        "maker_fee": None, "taker_fee": None,
                        "zero_fee": None,
                        "depth_at_bps": {},
                        "reason": str(item),
                        "reasons_all": [f"scan_error:{type(item).__name__}"],
                    })()
                    processed.append(stub_row)
            else:
                processed.append(item)
        all_rows.append((name, processed))

    if debug:
        for name, rows_list in all_rows:
            log.info("[%s] branch=%s rows=%d", req_id, name, len(rows_list))
            for r in rows_list[:5]:
                log.info("[%s] %s | %s", req_id, _preview_row(r), _row_fee_debug(r))

    # Warm cache (per venue)
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            gate_syms = [row.symbol for name, rows_list in all_rows for row in rows_list if name == "gate" and not isinstance(row, Exception)]
            mexc_syms = [row.symbol for name, rows_list in all_rows for row in rows_list if name == "mexc" and not isinstance(row, Exception)]
            if gate_syms:
                if iscoroutinefunction(func):
                    asyncio.create_task(func(gate_syms, venue="gate", concurrency=6))  # type: ignore[misc]
                else:
                    func(gate_syms, venue="gate", concurrency=6)  # type: ignore[misc]
            if mexc_syms:
                if iscoroutinefunction(func):
                    asyncio.create_task(func(mexc_syms, venue="mexc", concurrency=6))  # type: ignore[misc]
                else:
                    func(mexc_syms, venue="mexc", concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    candle_stats_map = {}
    if fetch_candles:
        tasks = []
        sym_to_venue: Dict[str, str] = {}
        for name, rows_list in all_rows:
            for row in rows_list:
                if not isinstance(row, Exception) and row.symbol != "**error**":
                    sym_to_venue[row.symbol] = name
                    tasks.append(asyncio.create_task(_fetch_candle_stats(row.symbol, venue=name)))
        if tasks:
            fetched = await asyncio.gather(*tasks, return_exceptions=True)
            candle_stats_map = {
                sym: (st if not isinstance(st, Exception) else {}) for sym, st in zip(sym_to_venue.keys(), fetched)
            }

    out: List[Dict[str, Any]] = []
    for name, rows_list in all_rows:
        for r in rows_list:
            if isinstance(r, Exception):
                continue
            stats = candle_stats_map.get(r.symbol, {}) if fetch_candles else {}
            out.append(_row_to_payload(r, exchange=name, explain=explain, rotation=rotation, candle_stats=stats))

    if exch == "all" and limit:
        out = sorted(out, key=lambda x: x.get("score", 0), reverse=True)[:limit]

    if rotation:
        _apply_rotation_queue(out)

    _observe_scan_latency(t0)
    _set_candidates(len(out))

    if debug:
        dur = time() - t0
        log.info("[%s] /top done in %.3fs out=%d", req_id, dur, len(out))

    return out


# ───────────── Gate: tiered ─────────────

@router.get("/gate/top_tiered", response_model=ScannerTopResponse)
async def gate_top_tiered(
    preset: str = Query("balanced", description='Профиль пресетов: "conservative" | "balanced" | "aggressive"'),
    quote: str = Query("USDT", description='Котируемая валюта: "USDT" | "USDC" | "FDUSD" | "BUSD" | "ALL"'),
    limit: int = Query(50, ge=1, le=200),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0),
    depth_bps_levels: str = Query("5,10"),
    min_depth5_usd: float = Query(0.0, ge=0.0),
    min_depth10_usd: float = Query(0.0, ge=0.0),
    min_trades_per_min: float = Query(0.0, ge=0.0),
    min_usd_per_min: float = Query(0.0, ge=0.0),
    include_stables: bool = Query(False),
    exclude_leveraged: bool = Query(True),
    explain: bool = Query(False),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    debug: bool = Query(False, description="Log verbose debug info for this request (server logs only)"),
):
    req_id = _new_req_id()
    t0 = time()
    _tick_top_request()

    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote_in = quote
    quote = (quote or "USDT").upper()
    if quote not in {"USDT", "USDC", "FDUSD", "BUSD", "ALL"}:
        quote = "USDT"
    limit = max(1, min(200, int(limit)))
    levels = _parse_levels(depth_bps_levels)

    if debug:
        log.info("[%s] /gate/top_tiered start preset=%s quote=%s(raw=%s) limit=%s levels=%s", req_id, preset, quote, quote_in, limit, levels)

    try:
        rows = await _run_with_timeout(
            scan_gate_quote(
                quote=quote,
                limit=limit,
                min_quote_vol_usd=min_quote_vol_usd,
                min_spread_pct=min_spread_pct,
                include_stables=include_stables,
                exclude_leveraged=exclude_leveraged,
                depth_levels_bps=levels,
                min_depth5_usd=min_depth5_usd,
                min_depth10_usd=min_depth10_usd,
                min_trades_per_min=min_trades_per_min,
                min_usd_per_min=min_usd_per_min,
                explain=explain,
                use_cache=True,
                liquidity_test=liquidity_test,
                fetch_candles=True,  # tiered view benefits from richer stats
            ),
            timeout=SCAN_TIMEOUT_SEC,
            name="gate",
            req_id=req_id,
        )
    except HTTPException:
        rows = []

    rows = [r for r in rows if not isinstance(r, Exception)]
    rows = filter_blacklisted(rows)
    if not rows:
        _observe_top_latency(t0)
        if debug:
            log.info("[%s] /gate/top_tiered done (empty)", req_id)
        return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=[], tierB=[], excluded=[])

    if debug:
        log.info("[%s] gate tiered rows=%d (showing up to 5):", req_id, len(rows))
        for r in rows[:5]:
            log.info("[%s] %s | %s", req_id, _preview_row(r), _row_fee_debug(r))

    # прогрев свечей
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, venue="gate", concurrency=6))  # type: ignore[misc]
            else:
                func(syms, venue="gate", concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    # candle-метрики
    tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="gate")) for r in rows]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)
    stats_map = {r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)}

    def to_metrics(row) -> Metrics:
        depth5 = getattr(row, "depth_at_bps", {}).get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5_min_side = float(min(depth5.get("bid_usd", 0.0), depth5.get("ask_usd", 0.0)))
        cs = stats_map.get(row.symbol, {})
        trades_raw = float(getattr(row, "trades_per_min", 0.0) or 0.0)
        usd_pm = float(getattr(row, "usd_per_min", 0.0) or 0.0)
        median_trade = float(getattr(row, "median_trade_usd", 0.0) or 0.0)
        trades_per_min = trades_raw if trades_raw > 0 else (usd_pm / median_trade if usd_pm > 0 and median_trade > 0 else 0.0)
        eff_taker, _ = _pick_eff_spreads(row)
        eff_bps = float(eff_taker if eff_taker is not None else getattr(row, "spread_bps", 0.0) or 0.0)
        atr_val = float(cs.get("atr1m_pct", None) or 0.0)
        atr1m_pct = max(atr_val, p.min_atr1m_pct)
        return Metrics(
            usd_per_min=usd_pm,
            trades_per_min=trades_per_min,
            effective_spread_bps=eff_bps,
            slip_bps_clip=eff_bps,
            atr1m_pct=atr1m_pct,
            spike_count_90m=int(cs.get("spike_count_90m", 0)),
            pullback_median_retrace=float(cs.get("pullback_median_retrace", 0.35)),
            grinder_ratio=float(cs.get("grinder_ratio", min(0.30, p.max_grinder_ratio))),
            depth_usd_5bps=depth5_min_side,
            imbalance_sigma_hits_60m=int(cs.get("imbalance_sigma_hits_60m", 0)),
            ws_lag_ms=None,
            stale_sec=None,
        )

    tierA: List[FeatureSnapshot] = []
    tierB: List[FeatureSnapshot] = []
    excluded: List[FeatureSnapshot] = []

    for r in rows:
        snap = snapshot_from_metrics(
            venue="gate",
            symbol=r.symbol,
            preset_name=preset,
            metrics=to_metrics(r),
            fees=_fees_from_row(r),
        )
        (tierA if snap.tier == "A" else tierB if snap.tier == "B" else excluded).append(snap)

    _observe_top_latency(t0)
    _set_candidates(len(tierA) + len(tierB))

    if debug:
        dur = time() - t0
        log.info("[%s] /gate/top_tiered done in %.3fs A=%d B=%d excl=%d", req_id, dur, len(tierA), len(tierB), len(excluded))

    return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=tierA, tierB=tierB, excluded=excluded)


# ───────────── MEXC: tiered ─────────────

@router.get("/mexc/top_tiered", response_model=ScannerTopResponse)
async def mexc_top_tiered(
    preset: str = Query("balanced", description='Профиль пресетов: "conservative" | "balanced" | "aggressive"'),
    quote: str = Query("USDT", description='Котируемая валюта, напр. "USDT"'),
    limit: int = Query(50, ge=1, le=200),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0),
    depth_bps_levels: str = Query("5,10"),
    min_depth5_usd: float = Query(0.0, ge=0.0),
    min_depth10_usd: float = Query(0.0, ge=0.0),
    min_trades_per_min: float = Query(0.0, ge=0.0),
    min_usd_per_min: float = Query(0.0, ge=0.0),
    include_stables: bool = Query(False),
    exclude_leveraged: bool = Query(True),
    explain: bool = Query(False),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    debug: bool = Query(False, description="Log verbose debug info for this request (server logs only)"),
):
    req_id = _new_req_id()
    t0 = time()
    _tick_top_request()

    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote = (quote or "USDT").upper()
    limit = max(1, min(200, int(limit)))
    levels = _parse_levels(depth_bps_levels)

    if debug:
        log.info("[%s] /mexc/top_tiered start preset=%s quote=%s limit=%s levels=%s", req_id, preset, quote, limit, levels)

    try:
        rows = await _run_with_timeout(
            scan_mexc_quote(
                quote=quote,
                limit=limit,
                min_quote_vol_usd=min_quote_vol_usd,
                min_spread_pct=min_spread_pct,
                depth_levels_bps=levels,
                min_depth5_usd=min_depth5_usd,
                min_depth10_usd=min_depth10_usd,
                min_trades_per_min=min_trades_per_min,
                min_usd_per_min=min_usd_per_min,
                include_stables=include_stables,
                exclude_leveraged=exclude_leveraged,
                explain=explain,
                use_cache=True,
                liquidity_test=liquidity_test,
                fetch_candles=True,  # tiered view benefits from richer stats
            ),
            timeout=SCAN_TIMEOUT_SEC,
            name="mexc",
            req_id=req_id,
        )
    except HTTPException:
        rows = []

    rows = [r for r in rows if not isinstance(r, Exception)]
    rows = filter_blacklisted(rows)
    if not rows:
        _observe_top_latency(t0)
        if debug:
            log.info("[%s] /mexc/top_tiered done (empty)", req_id)
        return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=[], tierB=[], excluded=[])

    if debug:
        log.info("[%s] mexc tiered rows=%d (showing up to 5):", req_id, len(rows))
        for r in rows[:5]:
            log.info("[%s] %s | %s", req_id, _preview_row(r), _row_fee_debug(r))

    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, venue="mexc", concurrency=6))  # type: ignore[misc]
            else:
                func(syms, venue="mexc", concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="mexc")) for r in rows]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)
    stats_map = {r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)}

    def to_metrics(row) -> Metrics:
        depth5 = getattr(row, "depth_at_bps", {}).get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5_min_side = float(min(depth5.get("bid_usd", 0.0), depth5.get("ask_usd", 0.0)))
        cs = stats_map.get(row.symbol, {})
        trades_raw = float(getattr(row, "trades_per_min", 0.0) or 0.0)
        usd_pm = float(getattr(row, "usd_per_min", 0.0) or 0.0)
        median_trade = float(getattr(row, "median_trade_usd", 0.0) or 0.0)
        trades_per_min = trades_raw if trades_raw > 0 else (usd_pm / median_trade if usd_pm > 0 and median_trade > 0 else 0.0)
        eff_taker, _ = _pick_eff_spreads(row)
        eff_bps = float(eff_taker if eff_taker is not None else getattr(row, "spread_bps", 0.0) or 0.0)
        atr_val = float(cs.get("atr1m_pct", None) or 0.0)
        atr1m_pct = max(atr_val, p.min_atr1m_pct)
        return Metrics(
            usd_per_min=usd_pm,
            trades_per_min=trades_per_min,
            effective_spread_bps=eff_bps,
            slip_bps_clip=eff_bps,
            atr1m_pct=atr1m_pct,
            spike_count_90m=int(cs.get("spike_count_90m", 0)),
            pullback_median_retrace=float(cs.get("pullback_median_retrace", 0.35)),
            grinder_ratio=float(cs.get("grinder_ratio", min(0.30, p.max_grinder_ratio))),
            depth_usd_5bps=depth5_min_side,
            imbalance_sigma_hits_60m=int(cs.get("imbalance_sigma_hits_60m", 0)),
            ws_lag_ms=None,
            stale_sec=None,
        )

    tierA: List[FeatureSnapshot] = []
    tierB: List[FeatureSnapshot] = []
    excluded: List[FeatureSnapshot] = []

    for r in rows:
        snap = snapshot_from_metrics(
            venue="mexc",
            symbol=r.symbol,
            preset_name=preset,
            metrics=to_metrics(r),
            fees=_fees_from_row(r),
        )
        (tierA if snap.tier == "A" else tierB if snap.tier == "B" else excluded).append(snap)

    _observe_top_latency(t0)
    _set_candidates(len(tierA) + len(tierB))

    if debug:
        dur = time() - t0
        log.info("[%s] /mexc/top_tiered done in %.3fs A=%d B=%d excl=%d", req_id, dur, len(tierA), len(tierB), len(excluded))

    return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=tierA, tierB=tierB, excluded=excluded)
