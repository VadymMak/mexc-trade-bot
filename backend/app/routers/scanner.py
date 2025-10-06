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


def _row_to_payload(r, *, exchange: str, explain: bool) -> Dict[str, Any]:
    """
    Формирует финальный объект ответа.
    Важные моменты:
      • используем eff_spread_* и score из ScanRow, если они уже посчитаны сервисом;
        если нет — корректно считаем локально (обратная совместимость).
      • пробрасываем reason и reasons_all при explain=true.
    """
    mid = (float(r.bid) + float(r.ask)) * 0.5 if (getattr(r, "bid", 0.0) and getattr(r, "ask", 0.0)) else float(getattr(r, "last", 0.0) or 0.0)  # noqa: E501

    # Effective spreads: предпочитаем предвычисленные поля из ScanRow
    eff_bps_taker = getattr(r, "eff_spread_taker_bps", None)
    eff_bps_maker = getattr(r, "eff_spread_maker_bps", None)

    # Фоллбэк: если сервис не заполнил — считаем совместимо с прежней логикой роутера
    if eff_bps_taker is None or eff_bps_maker is None:
        maker_fee = float(getattr(r, "maker_fee", 0.0) or 0.0)
        taker_fee = float(getattr(r, "taker_fee", 0.0) or 0.0)
        spread_bps = float(getattr(r, "spread_bps", 0.0) or 0.0)
        # старый «одиночный» вариант: raw spread + fee
        if eff_bps_taker is None:
            eff_bps_taker = spread_bps + taker_fee * 1e4
        if eff_bps_maker is None:
            eff_bps_maker = spread_bps + maker_fee * 1e4

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
        spread_penalty = float(eff_bps_taker or 0.0)  # штраф в bps (текущая норма)
        score = 2.0 * upm_term + 1.5 * depth_term - 0.05 * spread_penalty

    base: Dict[str, Any] = {
        "exchange": exchange,
        "symbol": r.symbol,
        "bid": r.bid,
        "ask": r.ask,
        "last": r.last,
        "spread_abs": r.spread_abs,
        "spread_pct": r.spread_pct,
        "spread_bps": r.spread_bps,
        "base_volume_24h": r.base_volume_24h,
        "quote_volume_24h": r.quote_volume_24h,
        "trades_per_min": r.trades_per_min,
        "usd_per_min": r.usd_per_min,
        "median_trade_usd": r.median_trade_usd,
        "imbalance": r.imbalance,
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

    # depth@5/10
    d5 = getattr(r, "depth_at_bps", {}) or {}
    d5obj = d5.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    base["depth5_bid_usd"] = d5obj.get("bid_usd", 0.0)
    base["depth5_ask_usd"] = d5obj.get("ask_usd", 0.0)
    d10 = d5.get(10, {"bid_usd": 0.0, "ask_usd": 0.0}) if 10 in d5 else getattr(r, "depth_at_bps", {}).get(10, {"bid_usd": 0.0, "ask_usd": 0.0})  # noqa: E501
    base["depth10_bid_usd"] = d10.get("bid_usd", 0.0)
    base["depth10_ask_usd"] = d10.get("ask_usd", 0.0)

    # дополнительные уровни, если есть
    for L, vals in getattr(r, "depth_at_bps", {}).items():
        if L in (5, 10):
            continue
        base[f"depth{L}_bid_usd"] = float(vals.get("bid_usd", 0.0))
        base[f"depth{L}_ask_usd"] = float(vals.get("ask_usd", 0.0))

    if explain:
        base["reason"] = getattr(r, "reason", None) or "ok"
        # пробрасываем подробности (если сервис заполнил)
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


async def _run_with_timeout(coro, *, timeout: float, name: str):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"{name} scan timed out after {timeout}s") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{name} scan failed: {type(e).__name__}") from e


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
):
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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="gate",
    )

    # негBlocking тёплый прогрев кэша свечей
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, concurrency=6))  # type: ignore[misc]
            else:
                func(syms, concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    return [_row_to_payload(r, exchange="gate", explain=explain) for r in rows]


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
):
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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="mexc",
    )
    return [_row_to_payload(r, exchange="mexc", explain=explain) for r in rows]


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
):
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
                ),
                timeout=SCAN_TIMEOUT_SEC,
            )
        ))

    names = [name for name, _ in tasks]
    awaits = [aw for _, aw in tasks]
    results = await asyncio.gather(*awaits, return_exceptions=True)

    out: List[Dict[str, Any]] = []
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            # частичная деградация — вернём stub, если explain=true
            if explain:
                out.append({
                    "exchange": name,
                    "symbol": "__error__",
                    "bid": 0.0, "ask": 0.0, "last": 0.0,
                    "spread_abs": 0.0, "spread_pct": 0.0, "spread_bps": 0.0,
                    "base_volume_24h": 0.0, "quote_volume_24h": 0.0,
                    "trades_per_min": 0.0, "usd_per_min": 0.0, "median_trade_usd": 0.0,
                    "imbalance": 0.5, "ws_lag_ms": None,
                    "maker_fee": None, "taker_fee": None, "zero_fee": None,
                    "depth5_bid_usd": 0.0, "depth5_ask_usd": 0.0,
                    "depth10_bid_usd": 0.0, "depth10_ask_usd": 0.0,
                    "eff_spread_bps": 0.0, "eff_spread_pct": 0.0, "eff_spread_abs": 0.0,
                    "eff_spread_bps_taker": 0.0, "eff_spread_pct_taker": 0.0, "eff_spread_abs_taker": 0.0,
                    "eff_spread_bps_maker": 0.0, "eff_spread_pct_maker": 0.0, "eff_spread_abs_maker": 0.0,
                    "score": 0.0,
                    "reason": f"{type(res).__name__}: {res}",
                    "reasons_all": [f"route_error:{type(res).__name__}"],
                })
            continue
        out.extend(_row_to_payload(r, exchange=name, explain=explain) for r in res)
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
):
    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote = (quote or "USDT").upper()
    if quote not in {"USDT", "USDC", "FDUSD", "BUSD", "ALL"}:
        quote = "USDT"
    limit = max(1, min(200, int(limit)))
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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="gate",
    )

    # прогрев свечей (не блокируем ответ)
    try:
        if hasattr(candles_cache, "touch_symbols"):
            func = getattr(candles_cache, "touch_symbols")
            syms = [r.symbol for r in rows]
            if iscoroutinefunction(func):
                asyncio.create_task(func(syms, concurrency=6))  # type: ignore[misc]
            else:
                func(syms, concurrency=6)  # type: ignore[misc]
    except Exception:
        pass

    # получение candle-метрик
    async def _fetch_stat(sym: str) -> Dict[str, Any]:
        if isinstance(candles_cache, dict):
            return dict(candles_cache.get(sym, {}))
        if hasattr(candles_cache, "get_stats"):
            func = getattr(candles_cache, "get_stats")
            try:
                if iscoroutinefunction(func):
                    res = await func(sym)  # type: ignore[misc]
                else:
                    res = func(sym)       # type: ignore[misc]
                return dict(res or {})
            except Exception:
                return {}
        if hasattr(candles_cache, "aget_stats"):
            try:
                res = await candles_cache.aget_stats(sym)  # type: ignore[attr-defined]
                return dict(res or {})
            except Exception:
                return {}
        if hasattr(candles_cache, "compute_metrics_gate"):
            try:
                res = await candles_cache.compute_metrics_gate(sym)  # type: ignore[attr-defined]
                return dict(res or {})
            except Exception:
                return {}
        return {}

    tasks = [asyncio.create_task(_fetch_stat(r.symbol)) for r in rows]
    fetched = await asyncio.gather(*tasks, return_exceptions=False)
    stats_map = {r.symbol: (st or {}) for r, st in zip(rows, fetched)}

    def to_metrics(row) -> Metrics:
        depth5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5_min_side = float(min(depth5.get("bid_usd", 0.0), depth5.get("ask_usd", 0.0)))
        cs = stats_map.get(row.symbol, {})
        return Metrics(
            usd_per_min=float(row.usd_per_min or 0.0),
            trades_per_min=float(row.trades_per_min or 0.0),
            effective_spread_bps=float(row.spread_bps or 0.0),
            slip_bps_clip=float(row.spread_bps or 0.0),
            atr1m_pct=float(cs.get("atr1m_pct", max(p.min_atr1m_pct * 0.9, 0.001))),
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
):
    try:
        p = get_preset(preset)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    quote = (quote or "USDT").upper()
    limit = max(1, min(200, int(limit)))
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
        ),
        timeout=SCAN_TIMEOUT_SEC,
        name="mexc",
    )

    # candle-метрики
    async def _fetch_stat(sym: str) -> Dict[str, Any]:
        if isinstance(candles_cache, dict):
            return dict(candles_cache.get(sym, {}))
        if hasattr(candles_cache, "get_stats"):
            func = getattr(candles_cache, "get_stats")
            try:
                if iscoroutinefunction(func):
                    res = await func(sym)  # type: ignore[misc]
                else:
                    res = func(sym)       # type: ignore[misc]
                return dict(res or {})
            except Exception:
                return {}
        if hasattr(candles_cache, "aget_stats"):
            try:
                res = await candles_cache.aget_stats(sym)  # type: ignore[attr-defined]
                return dict(res or {})
            except Exception:
                return {}
        return {}

    fetched = await asyncio.gather(*[asyncio.create_task(_fetch_stat(r.symbol)) for r in rows], return_exceptions=False)
    stats_map = {r.symbol: (st or {}) for r, st in zip(rows, fetched)}

    def to_metrics(row) -> Metrics:
        depth5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
        depth5_min_side = float(min(depth5.get("bid_usd", 0.0), depth5.get("ask_usd", 0.0)))
        cs = stats_map.get(row.symbol, {})
        return Metrics(
            usd_per_min=float(row.usd_per_min or 0.0),
            trades_per_min=float(row.trades_per_min or 0.0),
            effective_spread_bps=float(row.spread_bps or 0.0),
            slip_bps_clip=float(row.spread_bps or 0.0),
            atr1m_pct=float(cs.get("atr1m_pct", max(p.min_atr1m_pct * 0.9, 0.001))),
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

    return ScannerTopResponse(ts=int(time() * 1000), preset=preset, tierA=tierA, tierB=tierB, excluded=excluded)
