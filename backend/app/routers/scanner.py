# app/routers/scanner.py
from __future__ import annotations

import asyncio
import math
from inspect import iscoroutinefunction
from typing import List, Dict, Any, Tuple, Awaitable
from time import time

from fastapi import APIRouter, Query, HTTPException

from app.services.market_scanner import (
    scan_gate_quote,
    scan_mexc_quote,
)

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

# ---- жёсткие таймауты роутов ----
SCAN_TIMEOUT_SEC = 12   # верхняя граница на скан по бирже
CANDLE_TIMEOUT_SEC = 4  # таймаут на получение candle-статистик для одного символа


# ────────────────────────────── metrics helpers ──────────────────────────────

def _tick_scan_request() -> None:
    """Increments /scan-like request counters safely."""
    if not m:
        return
    try:
        m.api_scan_requests_total.inc()
    except Exception:
        pass


def _tick_top_request() -> None:
    """Increments /top(_tiered) request counters safely."""
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
        # gauge: last observed number of candidates (final payload length)
        m.scanner_candidates.set(float(max(0, int(n))))
    except Exception:
        pass


# ────────────────────────────── helpers ──────────────────────────────

def _parse_levels(csv: str) -> List[int]:
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
    return sorted(set(levels))


async def _fetch_candle_stats(sym: str, venue: str | None = None) -> Dict[str, Any]:
    """Unified async fetcher for candle stats, with timeouts, fallbacks, and venue-specific methods."""
    if not candles_cache:
        return {}
    try:
        if isinstance(candles_cache, dict):
            return dict(candles_cache.get(sym, {}))
        # prefer async API
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
                loop = asyncio.get_event_loop()
                if venue is not None:
                    res = await loop.run_in_executor(None, lambda: func(sym, venue=venue))
                else:
                    res = await loop.run_in_executor(None, lambda: func(sym))
            return dict(res or {})
        # Venue-specific compute
        compute_attr = f"compute_metrics_{venue}" if venue else None
        if compute_attr and hasattr(candles_cache, compute_attr):
            res = await asyncio.wait_for(getattr(candles_cache, compute_attr)(sym), timeout=CANDLE_TIMEOUT_SEC)
            return dict(res or {})
        # Generic fallback(s)
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


def _pick_eff_spreads(r) -> Tuple[float | None, float | None]:
    """
    Берём предвычисленные поля, учитывая возможные варианты имён:
      • eff_spread_bps_taker / eff_spread_bps_maker (нормальные)
      • eff_spread_taker_bps / eff_spread_maker_bps (перепутанные)
    """
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
    rotation: bool = False,  # флаг оставлен для обратной совместимости, очередь теперь ставится после сборки всего списка
    candle_stats: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """
    Формирует финальный объект ответа.
    Важные моменты:
      • используем eff_spread_* и score из ScanRow, если они уже посчитаны сервисом;
        если нет — корректно считаем локально (обратная совместимость).
      • пробрасываем reason и reasons_all при explain=true.
      • candle_stats (если есть) добавляет ATR/спайки/шэйп и влияет на локальный score.
    """
    mid = (float(getattr(r, "bid", 0.0) or 0.0) + float(getattr(r, "ask", 0.0) or 0.0)) * 0.5
    if mid <= 0:
        mid = float(getattr(r, "last", 0.0) or 0.0)

    # Effective spreads: предпочитаем предвычисленные поля из ScanRow
    eff_bps_taker, eff_bps_maker = _pick_eff_spreads(r)

    # Фоллбэк: если сервис не заполнил — считаем из raw spread + fee (правильная формула)
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

    # score: предпочитаем r.score, при отсутствии — считаем локально
    score = getattr(r, "score", None)
    if score is None:
        d5 = getattr(r, "depth_at_bps", {}) or {}
        d5obj = d5.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5 = float(min(d5obj.get("bid_usd", 0.0), d5obj.get("ask_usd", 0.0)))
        upm = float(getattr(r, "usd_per_min", 0.0) or 0.0)
        # лог-скейлы стабилизируют порядок
        upm_term = math.log10(upm + 10.0)
        depth_term = math.log10(depth5 + 10.0)
        spread_penalty = float(eff_bps_taker or 0.0)  # штраф в bps
        score = 2.0 * upm_term + 1.5 * depth_term - 0.05 * spread_penalty
        # ATR-aware поправка
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
        # effective spread (алиасы по умолчанию → taker)
        "eff_spread_bps": eff_bps_taker,
        "eff_spread_pct": eff_pct_taker,
        "eff_spread_abs": eff_abs_taker,
        "eff_spread_bps_taker": eff_bps_taker,
        "eff_spread_pct_taker": eff_pct_taker,
        "eff_spread_abs_taker": eff_abs_taker,
        "eff_spread_bps_maker": eff_bps_maker,
        "eff_spread_pct_maker": eff_pct_maker,
        "eff_spread_abs_maker": eff_abs_maker,
        # composite score
        "score": score,
    }

    # Candle metrics (если есть)
    if candle_stats:
        base.update({
            "atr1m_pct": candle_stats.get("atr1m_pct"),
            "spike_count_90m": candle_stats.get("spike_count_90m"),
            "pullback_median_retrace": candle_stats.get("pullback_median_retrace"),
            "grinder_ratio": candle_stats.get("grinder_ratio"),
            "imbalance_sigma_hits_60m": candle_stats.get("imbalance_sigma_hits_60m"),
        })

    # depth@5/10 (+доп. уровни если есть)
    depth_map = getattr(r, "depth_at_bps", {}) or {}
    d5 = depth_map.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    d10 = depth_map.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})
    base["depth5_bid_usd"] = float(d5.get("bid_usd", 0.0))
    base["depth5_ask_usd"] = float(d5.get("ask_usd", 0.0))
    base["depth10_bid_usd"] = float(d10.get("bid_usd", 0.0))
    base["depth10_ask_usd"] = float(d10.get("ask_usd", 0.0))
    for L, vals in depth_map.items():
        if L in (5, 10):
            continue
        base[f"depth{L}_bid_usd"] = float(vals.get("bid_usd", 0.0))
        base[f"depth{L}_ask_usd"] = float(vals.get("ask_usd", 0.0))

    if explain:
        base["reason"] = getattr(r, "reason", None) or "ok"
        reasons_all = getattr(r, "reasons_all", None)
        if isinstance(reasons_all, list):
            base["reasons_all"] = reasons_all

    # queue добавляется ПОСЛЕ сборки всего списка (см. роуты), здесь не трогаем
    return base


def _fees_from_row(row) -> FeeInfo:
    return FeeInfo(
        maker=getattr(row, "maker_fee", None),
        taker=getattr(row, "taker_fee", None),
        zero_maker=bool(getattr(row, "zero_fee", False)),
    )


async def _run_with_timeout(coro, *, timeout: float, name: str):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"{name} scan timed out after {timeout}s") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{name} scan failed: {str(e)}") from e


def _apply_rotation_queue(payloads: List[Dict[str, Any]], *, top_n: int = 3) -> None:
    """Добавляет одинаковое поле queue=[...] в каждый элемент списка, где [...] — top-N по score во всей выдаче."""
    if not payloads:
        return
    ranked = sorted(payloads, key=lambda x: (x.get("score") or 0.0), reverse=True)[:top_n]
    queue = [p["symbol"] for p in ranked]
    for p in payloads:
        p["queue"] = queue


# ────────────────────────────── Gate: raw list ──────────────────────────────

@router.get("/gate/top", response_model=List[ScannerRow])
async def gate_top(
    quote: str = Query("USDT", description='Котируемая валюта: "USDT" | "USDC" | "FDUSD" | "BUSD" | "ALL"'),
    limit: int = Query(100, ge=1, le=500, description="Макс. число результатов"),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0, description="Мин. 24h объём по котируемой валюте"),
    depth_bps_levels: str = Query("5,10", description='CSV уровней глубины в bps, напр. "3,5,10"'),
    min_depth5_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±5 bps"),
    min_depth10_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±10 bps"),
    min_trades_per_min: float = Query(0.0, ge=0.0, description="Мин. число сделок/мин (окно ~60s)"),
    min_usd_per_min: float = Query(0.0, ge=0.0, description="Мин. оборот USD/мин (окно ~60s)"),
    include_stables: bool = Query(False, description="Включать пары с базой-стейблом"),
    exclude_leveraged: bool = Query(True, description="Исключать 3L/3S/UP/DOWN токены"),
    explain: bool = Query(False, description="Добавить краткую причину прохождения/отсева"),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
):
    t0 = time()
    _tick_scan_request()

    quote = (quote or "USDT").upper()
    if quote not in {"USDT", "USDC", "FDUSD", "BUSD", "ALL"}:
        quote = "USDT"
    limit = max(1, min(500, int(limit)))
    levels = _parse_levels(depth_bps_levels)

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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="gate",
    )

    # Process rows to handle any embedded exceptions (defensive)
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

    # Optional candle fetch
    candle_stats_map = {}
    if fetch_candles:
        tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="gate")) for r in rows]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        candle_stats_map = {r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)}

    payloads = [
        _row_to_payload(r, exchange="gate", explain=explain, rotation=rotation, candle_stats=candle_stats_map.get(r.symbol))
        for r in rows
    ]
    if rotation:
        _apply_rotation_queue(payloads)

    # metrics: latency + candidate count
    _observe_scan_latency(t0)
    _set_candidates(len(payloads))

    return payloads


# ────────────────────────────── MEXC: raw list ──────────────────────────────

@router.get("/mexc/top", response_model=List[ScannerRow])
async def mexc_top(
    quote: str = Query("USDT", description='Котируемая валюта: например "USDT"'),
    limit: int = Query(100, ge=1, le=500, description="Макс. число результатов"),
    min_spread_pct: float = Query(0.10, ge=0.0, description="(LEGACY) Макс. спред в %, 0.10 = 10 bps"),
    min_quote_vol_usd: float = Query(50_000, ge=0.0, description="Мин. 24h объём (quote)"),
    depth_bps_levels: str = Query("5,10", description="CSV уровней глубины в bps"),
    min_depth5_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±5 bps"),
    min_depth10_usd: float = Query(0.0, ge=0.0, description="Мин. USD на каждой стороне в пределах ±10 bps"),
    min_trades_per_min: float = Query(0.0, ge=0.0, description="Мин. число сделок/мин (окно ~60s)"),
    min_usd_per_min: float = Query(0.0, ge=0.0, description="Мин. оборот USD/мин (окно ~60s)"),
    include_stables: bool = Query(False, description="Включать пары со стейбл-базой"),
    exclude_leveraged: bool = Query(True, description="Исключать 3L/3S/UP/DOWN токены"),
    explain: bool = Query(False, description="Добавить причины фоллбеков/отсевов"),
    liquidity_test: bool = Query(False, description="Enforce liquidity grade >= B (exclude C)"),
    rotation: bool = Query(False, description="Return top-3 queue for bot rotation"),
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
):
    t0 = time()
    _tick_scan_request()

    quote = (quote or "USDT").upper()
    limit = max(1, min(500, int(limit)))
    levels = _parse_levels(depth_bps_levels)

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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="mexc",
    )

    # Process rows to handle any embedded exceptions (defensive)
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

    # Optional candle fetch
    candle_stats_map = {}
    if fetch_candles:
        tasks = [asyncio.create_task(_fetch_candle_stats(r.symbol, venue="mexc")) for r in rows]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        candle_stats_map = {r.symbol: (st if not isinstance(st, Exception) else {}) for r, st in zip(rows, fetched)}

    # тёплый прогрев кэша свечей (per venue)
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

    return payloads


# ────────────────────────────── Универсальный raw list ──────────────────────

@router.get("/top", response_model=List[ScannerRow])
async def top_any(
    exchange: str = Query("gate", description='Биржа: "gate" | "mexc" | "all"'),
    quote: str = Query("USDT", description='Котируемая валюта (для "all" действует на обе)'),
    limit: int = Query(100, ge=1, le=500),
    min_spread_pct: float = Query(0.10, ge=0.0),
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
    fetch_candles: bool = Query(False, description="Fetch additional candle metrics (ATR, spikes, vol patterns, etc.) for richer payloads"),
):
    t0 = time()
    _tick_scan_request()

    exch = (exchange or "gate").lower()
    if exch not in {"gate", "mexc", "all"}:
        exch = "gate"
    quote = (quote or "USDT").upper()
    levels = _parse_levels(depth_bps_levels)
    limit = max(1, min(500, int(limit)))

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
                ),
                timeout=SCAN_TIMEOUT_SEC,
            )
        ))

    names = [name for name, _ in tasks]
    awaits = [aw for _, aw in tasks]
    results = await asyncio.gather(*awaits, return_exceptions=True)

    all_rows: List[Tuple[str, Any]] = []  # (exchange, rows)
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
            continue

        # res is list; process to handle any embedded exceptions
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
                        "maker_fee": None, "taker_fee": None, "zero_fee": None,
                        "depth_at_bps": {},
                        "reason": str(item),
                        "reasons_all": [f"scan_error:{type(item).__name__}"],
                    })()
                    processed.append(stub_row)
            else:
                processed.append(item)
        all_rows.append((name, processed))

    # Warm cache for all symbols across exchanges (per venue)
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

    # Optional unified candle fetch across exchanges
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
                continue  # Already handled above
            stats = candle_stats_map.get(r.symbol, {}) if fetch_candles else {}
            out.append(_row_to_payload(r, exchange=name, explain=explain, rotation=rotation, candle_stats=stats))

    # Sort by score descending and apply limit for multi-exchange
    if exch == "all" and limit:
        out = sorted(out, key=lambda x: x.get("score", 0), reverse=True)[:limit]

    if rotation:
        _apply_rotation_queue(out)

    _observe_scan_latency(t0)
    _set_candidates(len(out))

    return out


# ────────────────────────────── Gate: tiered ────────────────────────────────

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
):
    t0 = time()
    _tick_top_request()

    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote = (quote or "USDT").upper()
    if quote not in {"USDT", "USDC", "FDUSD", "BUSD", "ALL"}:
        quote = "USDT"
    limit = max(1, min(200, int(limit)))
    levels = _parse_levels(depth_bps_levels)

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
            ),
            timeout=SCAN_TIMEOUT_SEC,
            name="gate",
        )
    except HTTPException:
        rows = []

    rows = [r for r in rows if not isinstance(r, Exception)]
    if not rows:
        _observe_top_latency(t0)
        return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=[], tierB=[], excluded=[])

    # прогрев свечей (не блокируем ответ)
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

    # получение candle-метрик
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
        # use effective taker spread if present
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

    return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=tierA, tierB=tierB, excluded=excluded)


# ────────────────────────────── MEXC: tiered ────────────────────────────────

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
):
    t0 = time()
    _tick_top_request()

    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote = (quote or "USDT").upper()
    limit = max(1, min(200, int(limit)))
    levels = _parse_levels(depth_bps_levels)

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
            ),
            timeout=SCAN_TIMEOUT_SEC,
            name="mexc",
        )
    except HTTPException:
        rows = []

    rows = [r for r in rows if not isinstance(r, Exception)]
    if not rows:
        _observe_top_latency(t0)
        return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=[], tierB=[], excluded=[])

    # прогрев свечей (не блокируем ответ)
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

    return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=tierA, tierB=tierB, excluded=excluded)
