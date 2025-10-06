from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from contextlib import suppress

import httpx

from app.config.settings import settings
from app.scoring.presets import PRESETS

# Try to re-export candles_cache for router fallback (optional)
try:
    from app.services.candles_cache import candles_cache  # noqa: F401
except Exception:
    candles_cache = None  # type: ignore

# ─────────────────────────── dataclass ───────────────────────────

@dataclass
class ScanRow:
    # identity
    symbol: str                 # e.g. "ETHUSDT"
    exchange: str = "gate"      # "gate" | "mexc"

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

    # fees (optional for gate, explicit for mexc)
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
    if mid <= 0 or x_bps <= 0:
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
        b = float(bid_qty or 0.0); a = float(ask_qty or 0.0)
        s = b + a
        return (b / s) if s > 0 else 0.5
    except Exception:
        return 0.5

# fees → bps
def _to_bps(fee_frac: Optional[float]) -> Optional[float]:
    if fee_frac is None:
        return None
    try:
        return float(fee_frac) * 1e4
    except Exception:
        return None

def _calc_effective_spreads(spread_bps: float, maker_fee: Optional[float], taker_fee: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Приближённая оценка round-trip cost:
      • taker: spread + 2 * taker_fee_bps
      • maker: max(spread - 2 * maker_fee_bps, 0)  (если maker_fee=0 — вы «перекрываете» спред)
    """
    maker_bps = _to_bps(maker_fee)
    taker_bps = _to_bps(taker_fee)

    eff_maker = max(spread_bps - 2.0 * maker_bps, 0.0) if maker_bps is not None else None
    eff_taker = spread_bps + 2.0 * taker_bps if taker_bps is not None else None
    return eff_maker, eff_taker

def _log1p_safe(x: float) -> float:
    try:
        return math.log1p(max(0.0, float(x)))
    except Exception:
        return 0.0

def _http_timeout(short: bool = False) -> httpx.Timeout:
    return httpx.Timeout(connect=1.5, read=(2.8 if short else 3.8), write=2.0, pool=1.0)

async def _with_timeout(awaitable, seconds: float, default=None):
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except Exception:
        return default


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
    Выставляет короткую row.reason и наполняет row.reasons_all подробностями.
    Не меняет решение о фильтрации — только объясняет текущее состояние.
    Приоритет:
      1) spread too wide
      2) low depth
      3) low turnover (usd/min)
      4) low tpm
      5) partial (если уже помечен ранее)
      6) ok
    """
    # уже пометили partial выше? Сохраним как дополнительную причину
    if row.reason == "partial" and explain:
        row.reasons_all.append("state:partial")

    d5 = row.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
    depth5_min = float(min(d5.get("bid_usd", 0.0), d5.get("ask_usd", 0.0)))
    tpm = float(row.trades_per_min or 0.0)
    usdpm = float(row.usd_per_min or 0.0)

    # spread cap
    if spread_cap_bps > 0 and row.spread_bps > spread_cap_bps:
        row.reason = "spread too wide"
        if explain:
            row.reasons_all.append(f"spread_bps:{row.spread_bps:.2f} > cap:{spread_cap_bps:.2f}")
        return

    # depth
    if min_depth5_usd and depth5_min < min_depth5_usd:
        row.reason = "low depth"
        if explain:
            row.reasons_all.append(f"depth5_min:{depth5_min:.1f} < min:{min_depth5_usd:.1f}")
        return

    # turnover
    if min_usd_per_min and usdpm < min_usd_per_min:
        row.reason = "low turnover"
        if explain:
            row.reasons_all.append(f"usd_per_min:{usdpm:.2f} < min:{min_usd_per_min:.2f}")
        return

    # tpm
    if min_trades_per_min and tpm < min_trades_per_min:
        row.reason = "low tpm"
        if explain:
            row.reasons_all.append(f"trades_per_min:{tpm:.2f} < min:{min_trades_per_min:.2f}")
        return

    # partial already set
    if row.reason == "partial":
        return

    # ok
    row.reason = "ok"
    if explain:
        row.reasons_all.append("state:ok")


# ─────────────────────────── Gate REST calls ─────────────────────

async def _gate_fetch_tickers(client: httpx.AsyncClient, quote: str) -> List[Dict[str, Any]]:
    r = await client.get("/spot/tickers", params={"currency_pair": "", "limit": 5000})
    r.raise_for_status()
    arr = r.json()
    if isinstance(arr, list):
        return [t for t in arr if isinstance(t, dict) and str(t.get("currency_pair", "")).endswith(f"_{quote.upper()}")]
    return []

async def _gate_fetch_order_book(client: httpx.AsyncClient, pair: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    r = await client.get("/spot/order_book", params={"currency_pair": pair, "limit": limit})
    if r.status_code != 200:
        return None
    j = r.json()
    return j if isinstance(j, dict) else None

async def _gate_fetch_trades(client: httpx.AsyncClient, pair: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    r = await client.get("/spot/trades", params={"currency_pair": pair, "limit": min(200, max(50, limit))})
    if r.status_code != 200:
        return None
    j = r.json()
    return [x for x in j] if isinstance(j, list) else None


# ─────────────────────────── MEXC REST calls ─────────────────────

_MEXC_DEFAULT_MAKER = 0.0       # 0%
_MEXC_DEFAULT_TAKER = 0.0005    # 5 bps

async def _mexc_fetch_book_tickers(client: httpx.AsyncClient, quote: str) -> List[Dict[str, Any]]:
    r = await client.get("/api/v3/ticker/bookTicker")
    r.raise_for_status()
    arr = r.json()
    if not isinstance(arr, list):
        return []
    q = quote.upper()
    return [t for t in arr if isinstance(t, dict) and str(t.get("symbol", "")).upper().endswith(q)]

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
    r = await client.get("/api/v3/depth", params={"symbol": symbol, "limit": limit})
    if r.status_code != 200:
        return None
    j = r.json()
    return j if isinstance(j, dict) else None

async def _mexc_fetch_trades(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> Optional[List[Dict[str, Any]]]:
    r = await client.get("/api/v3/trades", params={"symbol": symbol, "limit": min(200, max(50, limit))})
    if r.status_code != 200:
        return None
    j = r.json()
    return [x for x in j] if isinstance(j, list) else None


# ─────────────────────────── Основной сканер: Gate ───────────────

_AVAILABLE_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")

def _apply_stage1_fields_and_effective(row: ScanRow) -> None:
    # пересчитываем эффективные спреды и last (если надо)
    row.last = row.last or (row.bid + row.ask) * 0.5
    row.spread_abs = max(0.0, (row.ask - row.bid))
    mid = (row.bid + row.ask) * 0.5
    row.spread_pct = (row.spread_abs / mid) * 100.0 if mid > 0 else 0.0
    row.spread_bps = (row.spread_abs / mid) * 1e4 if mid > 0 else 0.0
    eff_m, eff_t = _calc_effective_spreads(row.spread_bps, row.maker_fee, row.taker_fee)
    row.eff_spread_maker_bps = eff_m
    row.eff_spread_taker_bps = eff_t

def _score_row(row: ScanRow, depth_key_bps: int = 5) -> float:
    # Веса можно задать в settings:
    w_usdpm = float(getattr(settings, "score_w_usd_per_min", 1.0))
    w_depth  = float(getattr(settings, "score_w_depth", 0.7))
    w_spread = float(getattr(settings, "score_w_spread", 0.5))
    w_eff    = float(getattr(settings, "score_w_eff", 0.6))

    d = row.depth_at_bps.get(depth_key_bps, {"bid_usd": 0.0, "ask_usd": 0.0})
    depth_min_side = min(d["bid_usd"], d["ask_usd"])

    usd_term   = w_usdpm * _log1p_safe(row.usd_per_min)
    depth_term = w_depth  * _log1p_safe(depth_min_side)
    # штрафуем эффективный такер-спред (если нет — обычный спред)
    eff_bps = row.eff_spread_taker_bps if row.eff_spread_taker_bps is not None else row.spread_bps
    spread_pen = w_spread * (row.spread_bps / 10.0) + w_eff * (eff_bps / 10.0)  # нормировка по 10 для стабильности

    return usd_term + depth_term - spread_pen

async def _scan_one_quote_gate(
    *,
    client: httpx.AsyncClient,
    quote: str,
    limit: int,
    min_quote_vol_usd: float,
    max_spread_bps: float,
    include_stables: bool,
    exclude_leveraged: bool,
) -> List[ScanRow]:
    tickers = await _gate_fetch_tickers(client, quote=quote)
    rows: List[ScanRow] = []

    for t in tickers:
        with suppress(Exception):
            pair = str(t.get("currency_pair", ""))
            if not pair.endswith(f"_{quote.upper()}"):
                continue
            base_ccy, _quote_ccy = _split_pair(pair)

            if exclude_leveraged and _looks_like_leveraged(base_ccy):
                continue
            if not include_stables and _is_stable(base_ccy):
                continue

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
                imbalance=0.5,
                maker_fee=None,
                taker_fee=None,
                zero_fee=None,
            )
            _apply_stage1_fields_and_effective(row)
            if max_spread_bps > 0.0 and row.spread_bps > max_spread_bps:
                continue
            rows.append(row)

    rows.sort(key=lambda x: (-x.quote_volume_24h, x.spread_bps))
    return rows[: max(limit * 4, 50)]  # shortlist for enrichment


async def scan_gate_quote(
    *,
    quote: str = "USDT",
    limit: int = 100,
    min_quote_vol_usd: float = 50_000,
    min_spread_pct: Optional[float] = None,
    max_spread_bps: Optional[float] = 10.0,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    depth_levels_bps: List[int] | None = None,
    min_depth5_usd: float = 0.0,
    min_depth10_usd: float = 0.0,
    min_trades_per_min: float = 0.0,
    min_usd_per_min: float = 0.0,
    min_median_trade_usd: float = 0.0,
    explain: bool = False,
    use_cache: bool = True,
) -> List[ScanRow]:
    """
    Gate scanner: Stage1 (24h + spread) → Stage2 (depth/tape) with short timeouts.
    Supports quote="ALL".
    """
    limit = max(1, min(500, int(limit)))
    levels = sorted({int(v) for v in (depth_levels_bps or [5, 10]) if int(v) > 0})
    if 5 not in levels: levels.append(5)
    if 10 not in levels: levels.append(10)
    levels.sort()

    if max_spread_bps is not None:
        spread_cap_bps = float(max_spread_bps)
    elif min_spread_pct is not None:
        spread_cap_bps = float(min_spread_pct) * 100.0
    else:
        spread_cap_bps = 10.0

    cache_key = (
        f"gate:{quote}:{limit}:{min_quote_vol_usd}:{spread_cap_bps}:{include_stables}:{exclude_leveraged}:"
        f"{tuple(levels)}:{min_depth5_usd}:{min_depth10_usd}:{min_trades_per_min}:{min_usd_per_min}:"
        f"{min_median_trade_usd}:{int(explain)}"
    )
    now = time.monotonic()
    if use_cache and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts <= _CACHE_TTL:
            return data[:limit]

    base_url = _gate_rest_base()
    headers = {"Accept": "application/json", "User-Agent": "scanner/1.0"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_http_timeout(False)) as cli:
        quotes_to_scan: List[str] = list(_AVAILABLE_QUOTES) if quote.upper() == "ALL" else [quote.upper()]

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
            )
            stage1_all.extend(rows_q)

        if not stage1_all:
            _CACHE[cache_key] = (now, [])
            return []

        shortlist = stage1_all[: min(len(stage1_all), max(limit * 2, 100))]
        sem = asyncio.Semaphore(12)

        async def _enrich_one(row: ScanRow) -> Optional[ScanRow]:
            async with sem:
                sym = row.symbol
                q = next((cand for cand in _AVAILABLE_QUOTES if sym.endswith(cand)), None) or quotes_to_scan[0]
                pair = _to_pair(sym, quote=q)
                mid = (row.bid + row.ask) * 0.5

                ob = await _with_timeout(_gate_fetch_order_book(cli, pair, limit=50), 1.2, default=None)
                bids: List[Tuple[float, float]] = []
                asks: List[Tuple[float, float]] = []
                imb = 0.5
                if isinstance(ob, dict):
                    for it in (ob.get("bids") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0: bids.append((p, qt))
                    bids.sort(key=lambda x: x[0], reverse=True)
                    for it in (ob.get("asks") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0: asks.append((p, qt))
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

                tr = await _with_timeout(_gate_fetch_trades(cli, pair, limit=120), 1.2, default=None)

                def _calc_window(minutes: int) -> Tuple[float, float, float]:
                    if not isinstance(tr, list) or not tr:
                        return 0.0, 0.0, 0.0
                    now_ms = int(time.time() * 1000)
                    wins_ms = minutes * 60_000
                    amts: List[float] = []; total_usd = 0.0; total_cnt = 0
                    for x in tr:
                        with suppress(Exception):
                            ts_ms = None
                            if "create_time_ms" in x: ts_ms = int(float(x.get("create_time_ms", 0)))
                            elif "create_time" in x:  ts_ms = int(float(x.get("create_time", 0)) * 1000)
                            if ts_ms is None or now_ms - ts_ms > wins_ms: continue
                            price = float(x.get("price") or 0.0)
                            amt   = float(x.get("amount") or 0.0)
                            if price <= 0 or amt <= 0: continue
                            notional = price * amt
                            if notional < 1.0: continue
                            amts.append(notional); total_usd += notional; total_cnt += 1
                    if total_cnt == 0: return 0.0, 0.0, 0.0
                    amts.sort(); median = amts[len(amts)//2]
                    return float(total_cnt)/float(minutes), float(total_usd)/float(minutes), float(median)

                tpm, usdpm, med = _calc_window(1)
                if tpm == 0.0 and usdpm == 0.0:
                    tpm, usdpm, med = _calc_window(5)
                    if (tpm > 0.0 or usdpm > 0.0) and explain:
                        row.reasons_all.append("fallback:tape_5m")

                if (tpm == 0.0 and usdpm == 0.0) and isinstance(tr, list) and tr:
                    with suppress(Exception):
                        ts = []
                        for x in tr:
                            if "create_time_ms" in x: ts.append(int(float(x["create_time_ms"])))
                            elif "create_time" in x: ts.append(int(float(x["create_time"]) * 1000))
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
                                            if n >= 1.0: notionals.append(n)
                                if notionals:
                                    avg_n = sum(notionals) / len(notionals)
                                    tpm = float(cnt) / span_min
                                    usdpm = (avg_n * float(cnt)) / span_min
                                    notionals.sort(); med = notionals[len(notionals)//2]
                                    if explain: row.reasons_all.append("fallback:tape_span")

                if tpm == 0.0 and usdpm == 0.0:
                    usdpm = float(row.quote_volume_24h) / 1440.0
                    tpm = 0.0; med = med or 0.0
                    if explain: row.reasons_all.append("fallback:24h_rate")

                row.depth_at_bps = depth_map
                row.trades_per_min = tpm
                row.usd_per_min = usdpm
                row.median_trade_usd = med
                row.imbalance = imb
                # reason выставим после классификации
                return row

        tasks = [asyncio.create_task(_enrich_one(r)) for r in shortlist]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        stage2: List[ScanRow] = []
        for res in enriched:
            if isinstance(res, ScanRow):
                # Классификация причин (до фильтров — чтобы reason была информативной)
                _classify_reason(
                    res,
                    min_depth5_usd=min_depth5_usd,
                    min_trades_per_min=min_trades_per_min,
                    min_usd_per_min=min_usd_per_min,
                    spread_cap_bps=spread_cap_bps,
                    explain=explain,
                )

                # фильтры
                d5 = res.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
                d10 = res.depth_at_bps.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})
                if min_depth5_usd and min(d5["bid_usd"], d5["ask_usd"]) < min_depth5_usd: continue
                if min_depth10_usd and min(d10["bid_usd"], d10["ask_usd"]) < min_depth10_usd: continue
                if min_trades_per_min and res.trades_per_min < min_trades_per_min: continue
                if min_usd_per_min and res.usd_per_min < min_usd_per_min: continue
                if min_median_trade_usd and res.median_trade_usd < min_median_trade_usd: continue

                # score
                res.score = _score_row(res, depth_key_bps=5)
                stage2.append(res)

        stage2.sort(key=lambda x: (-(x.score if x.score is not None else -1e9)))
        out = stage2[:limit]
        if use_cache:
            _CACHE[cache_key] = (now, out)
        return out


# ─────────────────────────── Основной сканер: MEXC ───────────────

async def _scan_one_quote_mexc(
    *,
    client: httpx.AsyncClient,
    quote: str,
    limit: int,
    min_quote_vol_usd: float,
    max_spread_bps: float,
    include_stables: bool,
    exclude_leveraged: bool,
) -> List[ScanRow]:
    """
    Stage 1 (MEXC): bookTicker + 24h объёмы + max spread.
    Fast-path: если settings.symbols заданы — берём только их.
    """
    book_all = await _with_timeout(_mexc_fetch_book_tickers(client, quote=quote), 3.0, default=[])
    symbols_hint: List[str] = []
    try:
        symbols_hint = [s.upper() for s in (getattr(settings, "symbols", []) or []) if str(s).strip()]
        symbols_hint = [s for s in symbols_hint if s.endswith(quote.upper())]
    except Exception:
        symbols_hint = []

    book = [t for t in book_all if str(t.get("symbol", "")).upper() in symbols_hint] if symbols_hint else book_all[:50]
    symbols = [str(x.get("symbol", "")).upper() for x in book if isinstance(x, dict)]
    vols24 = await _with_timeout(_mexc_fetch_24h(client, symbols), 3.0, default={})

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
                imbalance=0.5,
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
    min_quote_vol_usd: float = 50_000,
    min_spread_pct: Optional[float] = None,
    max_spread_bps: Optional[float] = 10.0,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    depth_levels_bps: List[int] | None = None,
    min_depth5_usd: float = 0.0,
    min_depth10_usd: float = 0.0,
    min_trades_per_min: float = 0.0,
    min_usd_per_min: float = 0.0,
    min_median_trade_usd: float = 0.0,
    explain: bool = False,
    use_cache: bool = True,
) -> List[ScanRow]:
    """
    MEXC scanner (Spot V3):
      • Stage 1: bookTicker + 24h volumes, MAX spread filter.
      • Stage 2: depth@bps и tape через REST c короткими таймаутами.
      • Fast-path: если settings.symbols заданы — сканируем прежде всего их.
    """
    limit = max(1, min(500, int(limit)))
    levels = sorted({int(v) for v in (depth_levels_bps or [5, 10]) if int(v) > 0})
    if 5 not in levels: levels.append(5)
    if 10 not in levels: levels.append(10)
    levels.sort()

    if max_spread_bps is not None:
        spread_cap_bps = float(max_spread_bps)
    elif min_spread_pct is not None:
        spread_cap_bps = float(min_spread_pct) * 100.0
    else:
        spread_cap_bps = 10.0

    cache_key = (
        f"mexc:{quote}:{limit}:{min_quote_vol_usd}:{spread_cap_bps}:{include_stables}:{exclude_leveraged}:"
        f"{tuple(levels)}:{min_depth5_usd}:{min_depth10_usd}:{min_trades_per_min}:{min_usd_per_min}:"
        f"{min_median_trade_usd}:{int(explain)}"
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
            quote=quote.upper(),
            limit=limit,
            min_quote_vol_usd=min_quote_vol_usd,
            max_spread_bps=spread_cap_bps,
            include_stables=include_stables,
            exclude_leveraged=exclude_leveraged,
        )
        if not stage1_all:
            _CACHE[cache_key] = (now, [])
            return []

        shortlist = stage1_all[: min(len(stage1_all), max(limit * 2, 100))]
        sem = asyncio.Semaphore(10)

        async def _enrich_one(row: ScanRow) -> Optional[ScanRow]:
            async with sem:
                sym = row.symbol
                mid = (row.bid + row.ask) * 0.5

                ob = await _with_timeout(_mexc_fetch_depth(cli, sym, limit=50), 1.2, default=None)
                bids: List[Tuple[float, float]] = []
                asks: List[Tuple[float, float]] = []
                imb = 0.5
                if isinstance(ob, dict):
                    for it in (ob.get("bids") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0: bids.append((p, qt))
                    bids.sort(key=lambda x: x[0], reverse=True)
                    for it in (ob.get("asks") or []):
                        with suppress(Exception):
                            p = float(it[0]); qt = float(it[1])
                            if p > 0 and qt > 0: asks.append((p, qt))
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

                tr = await _with_timeout(_mexc_fetch_trades(cli, sym, limit=120), 1.2, default=None)
                if tr is None and explain:
                    row.reasons_all.append("timeout:tape")

                def _calc_window(minutes: int) -> Tuple[float, float, float]:
                    if not isinstance(tr, list) or not tr:
                        return 0.0, 0.0, 0.0
                    now_ms = int(time.time() * 1000)
                    wins_ms = minutes * 60_000
                    amts: List[float] = []; total_usd = 0.0; total_cnt = 0
                    for x in tr:
                        with suppress(Exception):
                            ts = int(x.get("time") or x.get("T") or 0)
                            if ts <= 0 or now_ms - ts > wins_ms: continue
                            price = float(x.get("price") or 0.0)
                            qty = float(x.get("qty") or 0.0)
                            if price <= 0 or qty <= 0: continue
                            notional = price * qty
                            if notional < 1.0: continue
                            amts.append(notional); total_usd += notional; total_cnt += 1
                    if total_cnt == 0: return 0.0, 0.0, 0.0
                    amts.sort(); median = amts[len(amts)//2]
                    return float(total_cnt)/float(minutes), float(total_usd)/float(minutes), float(median)

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
                                            if n >= 1.0: notionals.append(n)
                                if notionals:
                                    avg_n = sum(notionals) / len(notionals)
                                    tpm = float(cnt) / span_min
                                    usdpm = (avg_n * float(cnt)) / span_min
                                    notionals.sort(); med = notionals[len(notionals)//2]
                                    if explain: row.reasons_all.append("fallback:tape_span")

                if tpm == 0.0 and usdpm == 0.0:
                    usdpm = float(row.quote_volume_24h) / 1440.0
                    tpm = 0.0; med = med or 0.0
                    if explain: row.reasons_all.append("fallback:24h_rate")

                row.depth_at_bps = depth_map
                row.trades_per_min = tpm
                row.usd_per_min = usdpm
                row.median_trade_usd = med
                row.imbalance = imb
                # reason выставим после классификации
                return row

        async def _guarded(task_coro):
            return await _with_timeout(task_coro, 0.9, default=None)

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
            else:
                candidate = row

            # Классификация причин (до фильтров)
            _classify_reason(
                candidate,
                min_depth5_usd=min_depth5_usd,
                min_trades_per_min=min_trades_per_min,
                min_usd_per_min=min_usd_per_min,
                spread_cap_bps=spread_cap_bps,
                explain=explain,
            )

            # фильтры
            d5 = candidate.depth_at_bps.get(5, {"bid_usd": 0.0, "ask_usd": 0.0})
            d10 = candidate.depth_at_bps.get(10, {"bid_usd": 0.0, "ask_usd": 0.0})
            if min_depth5_usd and min(d5["bid_usd"], d5["ask_usd"]) < min_depth5_usd: continue
            if min_depth10_usd and min(d10["bid_usd"], d10["ask_usd"]) < min_depth10_usd: continue
            if min_trades_per_min and candidate.trades_per_min < min_trades_per_min: continue
            if min_usd_per_min and candidate.usd_per_min < min_usd_per_min: continue
            if min_median_trade_usd and candidate.median_trade_usd < min_median_trade_usd: continue

            candidate.score = _score_row(candidate, depth_key_bps=5)
            stage2.append(candidate)

        stage2.sort(key=lambda x: (-(x.score if x.score is not None else -1e9)))
        out = stage2[:limit]
        if use_cache:
            _CACHE[cache_key] = (now, out)
        return out


# ─────────────────────────── Preset-friendly wrappers ────────────

async def scan_gate_with_preset(
    *,
    preset: str = "balanced",
    quote: str = "USDT",
    limit: int = 100,
    include_stables: bool = False,
    exclude_leveraged: bool = True,
    explain: bool = True,
    use_cache: bool = True,
) -> List[ScanRow]:
    ps = PRESETS.get(preset.lower()) or PRESETS["balanced"]

    min_usd_per_min = float(ps.get("min_usd_per_min", 0.0))
    min_trades_per_min = int(ps.get("min_trades_per_min", 0))
    max_spread_bps = float(ps.get("max_spread_bps", 10.0))
    min_depth5_usd = float(ps.get("min_depth5_usd", 0.0))

    min_depth10_usd = 0.0
    min_median_trade_usd = 0.0
    min_quote_vol_usd = (min_usd_per_min * 1440.0) if min_usd_per_min > 0 else 50_000.0

    return await scan_gate_quote(
        quote=quote,
        limit=limit,
        min_quote_vol_usd=min_quote_vol_usd,
        max_spread_bps=max_spread_bps,
        include_stables=include_stables,
        exclude_leveraged=exclude_leveraged,
        depth_levels_bps=[5, 10],
        min_depth5_usd=min_depth5_usd,
        min_depth10_usd=min_depth10_usd,
        min_trades_per_min=min_trades_per_min,
        min_usd_per_min=min_usd_per_min,
        min_median_trade_usd=min_median_trade_usd,
        explain=explain,
        use_cache=use_cache,
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
) -> List[ScanRow]:
    ps = PRESETS.get(preset.lower()) or PRESETS["balanced"]

    min_usd_per_min = float(ps.get("min_usd_per_min", 0.0))
    min_trades_per_min = int(ps.get("min_trades_per_min", 0))
    max_spread_bps = float(ps.get("max_spread_bps", 10.0))
    min_depth5_usd = float(ps.get("min_depth5_usd", 0.0))

    min_depth10_usd = 0.0
    min_median_trade_usd = 0.0
    min_quote_vol_usd = (min_usd_per_min * 1440.0) if min_usd_per_min > 0 else 50_000.0

    return await scan_mexc_quote(
        quote=quote,
        limit=limit,
        min_quote_vol_usd=min_quote_vol_usd,
        max_spread_bps=max_spread_bps,
        include_stables=include_stables,
        exclude_leveraged=exclude_leveraged,
        depth_levels_bps=[5, 10],
        min_depth5_usd=min_depth5_usd,
        min_depth10_usd=min_depth10_usd,
        min_trades_per_min=min_trades_per_min,
        min_usd_per_min=min_usd_per_min,
        min_median_trade_usd=min_median_trade_usd,
        explain=explain,
        use_cache=use_cache,
    )
