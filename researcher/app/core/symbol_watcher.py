"""
SymbolWatcher — daily discovery of new perpetual futures listings.

Queries all 4 exchanges for their full futures contract list,
finds pairs available on ≥ 2 exchanges, and scores them by:
  - Age: listed < 60 days → priority window
  - Volume: > $1M/day
  - Coverage: how many of our exchanges list this pair

Saves result to data/discovered_symbols.json which researcher/main.py
picks up automatically (no restart needed — checked every reload).

Run standalone:
    python -m app.core.symbol_watcher

Or scheduled inside main.py via asyncio.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

OUTPUT_FILE = Path(__file__).parent.parent / "data" / "discovered_symbols.json"

# Minimum volume in USDT/24h to be worth tracking
MIN_VOLUME_USDT = 1_000_000

# Age window: only flag as "new" if listed within this many days
NEW_LISTING_DAYS = 60

# Minimum exchanges that must list the pair (out of 4)
MIN_EXCHANGE_COVERAGE = 2

# Hardcoded fallback if discovery fails
FALLBACK_SYMBOLS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
]


# ── Per-exchange fetchers ─────────────────────────────────────────────────────

async def fetch_binance(session: aiohttp.ClientSession) -> dict[str, dict]:
    """
    Returns { "BTC_USDT": { "volume_usdt": float, "listed_ms": int|None } }
    """
    result: dict[str, dict] = {}
    try:
        # Exchange info (all perpetual symbols)
        async with session.get(
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()

        symbols_info: dict[str, int] = {}
        for s in data.get("symbols", []):
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
                sym = s["symbol"]  # e.g. BTCUSDT
                # onboardDate in ms
                listed_ms = s.get("onboardDate")
                # Normalise to our format: BTCUSDT → BTC_USDT
                norm = _binance_to_norm(sym)
                if norm:
                    symbols_info[norm] = listed_ms

        # 24h ticker for volume
        async with session.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            tickers = await r.json()

        vol_map: dict[str, float] = {}
        for t in tickers:
            norm = _binance_to_norm(t.get("symbol", ""))
            if norm:
                vol_map[norm] = float(t.get("quoteVolume", 0))

        for norm, listed_ms in symbols_info.items():
            result[norm] = {
                "volume_usdt": vol_map.get(norm, 0),
                "listed_ms":   listed_ms,
            }

    except Exception as exc:
        log.warning("[Watcher] Binance fetch error: %r", exc)
    return result


async def fetch_bybit(session: aiohttp.ClientSession) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        async with session.get(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "linear", "limit": 1000},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()

        info_map: dict[str, Optional[int]] = {}
        for s in data.get("result", {}).get("list", []):
            if s.get("quoteCoin") == "USDT" and s.get("status") == "Trading":
                sym = s["symbol"]  # BTCUSDT
                norm = _binance_to_norm(sym)  # same format
                if norm:
                    # launchTime in ms (string)
                    lt = s.get("launchTime")
                    info_map[norm] = int(lt) if lt else None

        # 24h tickers
        async with session.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            tdata = await r.json()

        vol_map: dict[str, float] = {}
        for t in tdata.get("result", {}).get("list", []):
            norm = _binance_to_norm(t.get("symbol", ""))
            if norm:
                vol_map[norm] = float(t.get("turnover24h", 0) or 0)

        for norm, listed_ms in info_map.items():
            result[norm] = {
                "volume_usdt": vol_map.get(norm, 0),
                "listed_ms":   listed_ms,
            }

    except Exception as exc:
        log.warning("[Watcher] Bybit fetch error: %r", exc)
    return result


async def fetch_gate(session: aiohttp.ClientSession) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        async with session.get(
            "https://api.gateio.ws/api/v4/futures/usdt/contracts",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            contracts = await r.json()

        for c in contracts:
            if not c.get("in_delisting", False):
                # Gate symbol: BTC_USDT already in our format
                sym = c.get("name", "")
                if not sym.endswith("_USDT"):
                    continue
                result[sym] = {
                    "volume_usdt": float(c.get("volume_24h_quote", 0) or 0),
                    "listed_ms":   None,  # Gate API doesn't expose listing date directly
                }
    except Exception as exc:
        log.warning("[Watcher] Gate fetch error: %r", exc)
    return result


async def fetch_mexc(session: aiohttp.ClientSession) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        async with session.get(
            "https://contract.mexc.com/api/v1/contract/detail",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()

        for c in data.get("data", []):
            if c.get("quoteCoin") == "USDT" and c.get("state") == 0:
                # MEXC symbol: BTC_USDT
                sym = c.get("symbol", "")
                if not sym.endswith("_USDT"):
                    continue
                result[sym] = {
                    "volume_usdt": float(c.get("vol24", 0) or 0),
                    "listed_ms":   None,
                }
    except Exception as exc:
        log.warning("[Watcher] MEXC fetch error: %r", exc)
    return result


# ── Core discovery logic ──────────────────────────────────────────────────────

async def discover_symbols(save: bool = True) -> list[str]:
    """
    Main entry point. Returns list of symbol strings (e.g. ["BTC_USDT", ...]).
    If save=True, writes to discovered_symbols.json.
    """
    now_ms = int(time.time() * 1000)
    new_cutoff_ms = now_ms - NEW_LISTING_DAYS * 86400 * 1000

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            fetch_binance(session),
            fetch_bybit(session),
            fetch_gate(session),
            fetch_mexc(session),
            return_exceptions=True,
        )

    exchange_names = ["binance", "bybit", "gate", "mexc"]
    per_exchange: list[dict[str, dict]] = []
    for name, r in zip(exchange_names, results):
        if isinstance(r, Exception):
            log.error("[Watcher] %s failed: %r", name, r)
            per_exchange.append({})
        else:
            per_exchange.append(r)
            log.info("[Watcher] %s: %d symbols", name, len(r))

    # Union of all symbols (valid only)
    all_symbols: set[str] = set()
    for ex in per_exchange:
        all_symbols.update(s for s in ex.keys() if _is_valid_symbol(s))

    scored: list[dict] = []
    for sym in all_symbols:
        coverage = sum(1 for ex in per_exchange if sym in ex)
        if coverage < MIN_EXCHANGE_COVERAGE:
            continue

        # Best volume across exchanges
        volumes = [ex[sym]["volume_usdt"] for ex in per_exchange if sym in ex]
        max_vol = max(volumes) if volumes else 0
        if max_vol < MIN_VOLUME_USDT:
            continue

        # Listing date (earliest known across exchanges)
        listed_mss = [
            ex[sym]["listed_ms"]
            for ex in per_exchange
            if sym in ex and ex[sym].get("listed_ms")
        ]
        listed_ms: Optional[int] = min(listed_mss) if listed_mss else None

        # Is it a new listing?
        is_new = listed_ms is not None and listed_ms >= new_cutoff_ms
        age_days: Optional[int] = (
            int((now_ms - listed_ms) / 86400_000) if listed_ms else None
        )

        # Score: new listings score higher
        score = coverage * 10 + min(max_vol / 1_000_000, 100)
        if is_new:
            score += 50  # big boost for new listings

        scored.append({
            "symbol":    sym,
            "coverage":  coverage,
            "volume_usdt": max_vol,
            "listed_ms": listed_ms,
            "age_days":  age_days,
            "is_new":    is_new,
            "score":     score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Split: new listings first, then top liquid pairs, total cap 100
    new_listings = [s["symbol"] for s in scored if s["is_new"]][:30]
    top_liquid   = [s["symbol"] for s in scored if not s["is_new"]][:70]
    final = new_listings + [s for s in top_liquid if s not in new_listings]
    final = final[:100]

    if not final:
        log.warning("[Watcher] No symbols found — using fallback")
        final = FALLBACK_SYMBOLS

    log.info(
        "[Watcher] Discovery complete: %d total (%d new listings, %d established)",
        len(final), len(new_listings), len(final) - len(new_listings),
    )

    if new_listings:
        log.info("[Watcher] New listings (<60d): %s", ", ".join(new_listings[:10]))

    if save:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbols":      final,
            "new_listings": new_listings,
            "total":        len(final),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "details":      scored[:100],
        }
        OUTPUT_FILE.write_text(json.dumps(payload, indent=2))
        log.info("[Watcher] Saved to %s", OUTPUT_FILE)

    return final


async def watch_loop(interval_hours: float = 24.0) -> None:
    """
    Background loop: re-runs discovery every interval_hours.
    First run is immediate.
    """
    log.info("[Watcher] Starting symbol watch loop (every %.0fh)", interval_hours)
    while True:
        try:
            await discover_symbols(save=True)
        except Exception as exc:
            log.error("[Watcher] Discovery error: %r", exc)
        await asyncio.sleep(interval_hours * 3600)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _binance_to_norm(sym: str) -> Optional[str]:
    """Convert BTCUSDT → BTC_USDT. Skips non-USDT pairs and invalid symbols."""
    if not sym.endswith("USDT"):
        return None
    base = sym[:-4]   # strip USDT
    if not base:
        return None
    # Skip: non-ASCII (Chinese chars etc), pure numbers, too short
    if not base.isascii() or base.isdigit() or len(base) < 2:
        return None
    return f"{base}_USDT"


def _is_valid_symbol(sym: str) -> bool:
    """Validate normalised symbol like BTC_USDT."""
    if not sym.endswith("_USDT"):
        return False
    base = sym[:-5]
    # Must be ASCII letters only (no digits-only, no Chinese, no special chars)
    return base.isascii() and base.replace("1000", "").isalpha() and len(base) >= 2


# ── Standalone run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(discover_symbols(save=True))
