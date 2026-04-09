"""
Researcher service — entry point.

Run from the researcher/ directory:
    python -m app.main
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .config import Settings
from .collectors.binance_collector import BinanceCollector
from .collectors.bybit_collector import BybitCollector
from .collectors.gate_collector import GateCollector
from .collectors.mexc_collector import MexcCollector
from .core.pair_promoter import PairPromoter
from .core.paper_trader import PaperTrader
from .core.spread_matrix import SpreadMatrix
from .core.symbol_watcher import watch_loop as symbol_watch_loop, discover_symbols
from .db.neon_db import NeonDB


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("researcher")

    # ── Symbols ───────────────────────────────────────────────────────────────
    symbols_file = Path(settings.SYMBOLS_FILE)
    if symbols_file.exists():
        try:
            discovered = json.loads(symbols_file.read_text())
            symbols    = discovered["symbols"]
            new_count  = len(discovered.get("new_listings", []))
            log.info(
                "Loaded %d symbols from %s (%d new listings, generated %s)",
                len(symbols), symbols_file, new_count, discovered.get("generated_at", "?"),
            )
        except Exception as exc:
            log.warning("Failed to load %s: %r — running discovery now", symbols_file, exc)
            symbols = await discover_symbols(save=True)
    else:
        log.info("No symbols file — running live discovery…")
        try:
            symbols = await discover_symbols(save=True)
            log.info("Discovery found %d symbols", len(symbols))
        except Exception as exc:
            log.warning("Discovery failed: %r — using env var fallback", exc)
            symbols = settings.symbols_list

    # ── Database ──────────────────────────────────────────────────────────────
    db = NeonDB(settings.NEON_DATABASE_URL)
    if settings.NEON_DATABASE_URL:
        try:
            await db.connect()
            log.info("Neon DB connected")
        except Exception as exc:
            log.error("Neon DB connect failed: %r — running in dry-run mode", exc)
    else:
        log.warning("No NEON_DATABASE_URL — running without DB (dry run, no persistence)")

    # ── Core components ───────────────────────────────────────────────────────
    matrix   = SpreadMatrix(max_lag_ms=settings.MAX_SPREAD_LAG_MS)
    trader   = PaperTrader(db=db, settings=settings)
    promoter = PairPromoter(db=db, settings=settings)

    matrix.add_callback(trader.on_spread)

    # Push spread snapshots to the trading bot every 5 s
    matrix.set_push_url(
        url=f"{settings.TRADING_BOT_URL}/api/arbitrage/internal/spread-update",
        interval_s=5,
    )

    # ── Collectors ────────────────────────────────────────────────────────────
    collectors = [
        BinanceCollector(),
        BybitCollector(),
        GateCollector(),
        MexcCollector(),
    ]
    for c in collectors:
        c.set_callback(matrix.on_price)

    results = await asyncio.gather(
        *[c.connect(symbols) for c in collectors],
        return_exceptions=True,
    )
    for c, r in zip(collectors, results):
        if isinstance(r, Exception):
            log.error("%s failed to connect: %r", c.name, r)
        else:
            log.info("%s connected OK", c.name)

    # ── Background loops ──────────────────────────────────────────────────────
    stats_push_url = f"{settings.TRADING_BOT_URL}/api/arbitrage/internal/stats-update"

    async def report_loop() -> None:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(60)
                spreads    = matrix.get_all_spreads()
                summary    = trader.session_summary()

                log.info(
                    "━━ 60s report ━━  tracked=%d  open=%d  closed=%d  net_pnl=%+.4f USDT",
                    len(spreads),
                    summary["open_positions"],
                    summary["total_closed"],
                    summary["total_net_pnl"],
                )

                # Top 5 spreads by size
                if spreads:
                    top = sorted(spreads, key=lambda x: x.get("spread_pct", 0), reverse=True)[:5]
                    for s in top:
                        z = f"{s['zscore']:.2f}" if s.get("zscore") is not None else "—"
                        log.info(
                            "  %-10s  %-8s→%-8s  spread=%.3f%%  z=%s",
                            s["symbol"], s["exchange_long"], s["exchange_short"],
                            s["spread_pct"], z,
                        )

                # Build per-pair stats list from open positions
                open_pairs = [
                    {
                        "symbol":        k[0],
                        "exchange_long":  k[1],
                        "exchange_short": k[2],
                        "status":         "open",
                        "entry_spread":   v.entry_spread,
                        "entry_zscore":   v.entry_zscore,
                    }
                    for k, v in trader._open.items()
                ]

                payload = {
                    "session": summary,
                    "pairs":   open_pairs,
                }

                try:
                    async with session.post(
                        stats_push_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status >= 400:
                            log.warning("[Stats push] HTTP %d", resp.status)
                except Exception as exc:
                    log.debug("[Stats push] Error: %r", exc)

    async def symbol_reload_loop() -> None:
        """
        Every 24h: re-discover symbols, compare with current list,
        reconnect collectors to NEW symbols only (hot reload — no restart needed).
        """
        while True:
            await asyncio.sleep(24 * 3600)
            log.info("[Watcher] Running 24h symbol refresh…")
            try:
                new_symbols = await discover_symbols(save=True)
            except Exception as exc:
                log.error("[Watcher] Refresh failed: %r", exc)
                continue

            current = set(symbols)
            added   = [s for s in new_symbols if s not in current]
            removed = [s for s in symbols     if s not in new_symbols]

            if not added and not removed:
                log.info("[Watcher] Symbol list unchanged (%d symbols)", len(new_symbols))
                continue

            log.info(
                "[Watcher] Symbol update: +%d added, -%d removed",
                len(added), len(removed),
            )
            if added:
                log.info("[Watcher] New symbols to subscribe: %s", ", ".join(added[:10]))

            # Subscribe collectors to newly added symbols only
            if added:
                sub_results = await asyncio.gather(
                    *[c.connect(added) for c in collectors],
                    return_exceptions=True,
                )
                for c, r in zip(collectors, sub_results):
                    if isinstance(r, Exception):
                        log.warning("[Watcher] %s re-subscribe failed: %r", c.name, r)
                    else:
                        log.info("[Watcher] %s subscribed to %d new symbols", c.name, len(added))

            # Update our local reference
            symbols.clear()
            symbols.extend(new_symbols)

    # Make symbols mutable for hot reload
    symbols = list(symbols)

    await asyncio.gather(
        report_loop(),
        matrix.push_loop(),
        promoter.run(),
        symbol_reload_loop(),   # 24h hot reload — no restart needed
    )


if __name__ == "__main__":
    asyncio.run(main())
