"""
Symbol discovery script — run once to find symbols tradeable on all 4 exchanges.

Usage (from researcher/ directory):
    python -m app.tools.symbol_discovery

Output: data/discovered_symbols.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("symbol_discovery")

# ─── Volume filters ───
MIN_VOLUME_MEXC = 5_000_000
MIN_VOLUME_GATE = 5_000_000
MIN_VOLUME_BINANCE = 10_000_000
MIN_VOLUME_BYBIT = 5_000_000

OUTPUT_PATH = Path(__file__).parent.parent.parent / "data" / "discovered_symbols.json"

# Known quote currencies for symbol normalization (longest match first)
_QUOTES = ["USDT", "BUSD", "USDC", "USD", "BTC", "ETH", "BNB"]


def normalize_symbol(raw: str) -> Optional[str]:
    """Convert BTCUSDT / BTC-USDT / BTC_USDT → BTC_USDT."""
    s = raw.upper().replace("-", "_")
    if "_" in s:
        # Already underscore format — validate it ends with a known quote
        parts = s.split("_")
        if len(parts) == 2 and parts[1] in _QUOTES:
            return s
        return None
    # No underscore: BTCUSDT → BTC_USDT
    for quote in _QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            base = s[: -len(quote)]
            return f"{base}_{quote}"
    return None


# ─── Fetchers ───

async def fetch_mexc(session: aiohttp.ClientSession) -> dict[str, float]:
    """Returns {normalized_symbol: volume_usd}."""
    url = "https://contract.mexc.com/api/v1/contract/detail"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
        data = await r.json(content_type=None)

    result: dict[str, float] = {}
    for item in data.get("data") or []:
        sym = normalize_symbol(item.get("symbol", ""))
        if not sym:
            continue
        vol = float(item.get("volumeOf24h") or 0)
        if vol >= MIN_VOLUME_MEXC:
            result[sym] = vol
    logger.info("[MEXC]    %d symbols above $%,.0f volume", len(result), MIN_VOLUME_MEXC)
    return result


async def fetch_gate(session: aiohttp.ClientSession) -> dict[str, float]:
    """Returns {normalized_symbol: volume_usd}. Paginates by 100."""
    result: dict[str, float] = {}
    offset = 0
    while True:
        url = (
            f"https://api.gateio.ws/api/v4/futures/usdt/contracts"
            f"?limit=100&offset={offset}"
        )
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            page = await r.json(content_type=None)
        if not page:
            break
        for item in page:
            sym = normalize_symbol(item.get("name", ""))
            if not sym:
                continue
            # volume_24h_settle is in quote (USDT)
            vol_raw = item.get("volume_24h_settle") or item.get("volume_24h_quote") or 0
            vol = float(vol_raw)
            if vol >= MIN_VOLUME_GATE:
                result[sym] = vol
        if len(page) < 100:
            break
        offset += 100
    logger.info("[Gate]    %d symbols above $%,.0f volume", len(result), MIN_VOLUME_GATE)
    return result


async def fetch_binance(session: aiohttp.ClientSession) -> dict[str, float]:
    """Returns {normalized_symbol: volume_usd}."""
    # Step 1: active USDT perpetuals
    async with session.get(
        "https://fapi.binance.com/fapi/v1/exchangeInfo",
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        info = await r.json(content_type=None)

    active = {
        s["symbol"]
        for s in info.get("symbols", [])
        if s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    }

    # Step 2: 24h tickers
    async with session.get(
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        tickers = await r.json(content_type=None)

    result: dict[str, float] = {}
    for t in tickers:
        raw = t.get("symbol", "")
        if raw not in active:
            continue
        sym = normalize_symbol(raw)
        if not sym:
            continue
        vol = float(t.get("quoteVolume") or 0)
        if vol >= MIN_VOLUME_BINANCE:
            result[sym] = vol
    logger.info("[Binance] %d symbols above $%,.0f volume", len(result), MIN_VOLUME_BINANCE)
    return result


async def fetch_bybit(session: aiohttp.ClientSession) -> dict[str, float]:
    """Returns {normalized_symbol: volume_usd}."""
    # Step 1: active linear perpetuals (paginate)
    active: set[str] = set()
    cursor = ""
    while True:
        url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
        items = (data.get("result") or {}).get("list") or []
        for s in items:
            if (
                s.get("quoteCoin") == "USDT"
                and s.get("contractType") == "LinearPerpetual"
                and s.get("status") == "Trading"
            ):
                active.add(s["symbol"])
        cursor = (data.get("result") or {}).get("nextPageCursor", "")
        if not cursor or not items:
            break

    # Step 2: 24h tickers
    async with session.get(
        "https://api.bybit.com/v5/market/tickers?category=linear",
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        ticker_data = await r.json(content_type=None)

    result: dict[str, float] = {}
    for t in (ticker_data.get("result") or {}).get("list") or []:
        raw = t.get("symbol", "")
        if raw not in active:
            continue
        sym = normalize_symbol(raw)
        if not sym:
            continue
        vol = float(t.get("turnover24h") or 0)
        if vol >= MIN_VOLUME_BYBIT:
            result[sym] = vol
    logger.info("[Bybit]   %d symbols above $%,.0f volume", len(result), MIN_VOLUME_BYBIT)
    return result


# ─── Main logic ───

async def discover() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Fetching symbols from all 4 exchanges…")

    async with aiohttp.ClientSession() as session:
        mexc_syms, gate_syms, binance_syms, bybit_syms = await asyncio.gather(
            fetch_mexc(session),
            fetch_gate(session),
            fetch_binance(session),
            fetch_bybit(session),
        )

    exchange_maps = {
        "mexc": mexc_syms,
        "gate": gate_syms,
        "binance": binance_syms,
        "bybit": bybit_syms,
    }

    all_symbols: set[str] = set()
    for syms in exchange_maps.values():
        all_symbols |= syms.keys()

    # Build per-symbol detail
    details: dict[str, dict] = {}
    for sym in all_symbols:
        exchanges_present = [ex for ex, syms in exchange_maps.items() if sym in syms]
        if len(exchanges_present) < 3:
            continue
        volumes = [exchange_maps[ex][sym] for ex in exchanges_present]
        details[sym] = {
            "exchanges": sorted(exchanges_present),
            "min_volume_usd": int(min(volumes)),
        }

    # Sort by min volume descending
    sorted_symbols = sorted(
        details.keys(),
        key=lambda s: details[s]["min_volume_usd"],
        reverse=True,
    )

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_found": len(sorted_symbols),
        "symbols": sorted_symbols,
        "details": {s: details[s] for s in sorted_symbols},
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    logger.info("Saved → %s", OUTPUT_PATH)

    # ─── Print summary table ───
    print(f"\n{'Symbol':<14} {'Exchanges':<36} {'Min Vol (USD)':>15}")
    print("-" * 68)
    for sym in sorted_symbols[:60]:  # cap at 60 rows for readability
        d = details[sym]
        exs = ", ".join(d["exchanges"])
        vol = d["min_volume_usd"]
        print(f"{sym:<14} {exs:<36} {vol:>15,.0f}")

    if len(sorted_symbols) > 60:
        print(f"  … and {len(sorted_symbols) - 60} more (see {OUTPUT_PATH})")

    print(f"\nTotal: {len(sorted_symbols)} symbols on 3+ exchanges.")
    print(f"Saved to: {OUTPUT_PATH}\n")


if __name__ == "__main__":
    asyncio.run(discover())
