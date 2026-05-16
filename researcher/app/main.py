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
from .collectors.kucoin_collector import KucoinCollector
from .collectors.mexc_collector import MexcCollector
from .collectors.mexc_spot_collector import MexcSpotCollector
from .core.market_flow import FlowTracker, MexcFlowCollector, GateFlowCollector
from .core.pair_promoter import PairPromoter
from .core.paper_trader import PaperTrader
from .core.scalp_trader import ScalpPaperTrader
from .live.arb_executor import ArbLiveExecutor
from .live.mexc_futures import MexcFutures
from .live.gate_futures import GateFutures
from .live.mock_futures import MockFutures
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
            stale = await db.cleanup_stale_positions(max_hold_seconds=settings.MAX_HOLD_SECONDS)
            if stale > 0:
                log.warning("[DB] Cleaned %d stale open positions", stale)
        except Exception as exc:
            log.error("Neon DB connect failed: %r — running in dry-run mode", exc)
    else:
        log.warning("No NEON_DATABASE_URL — running without DB (dry run, no persistence)")

    # ── Core components ───────────────────────────────────────────────────────
    matrix       = SpreadMatrix(max_lag_ms=settings.MAX_SPREAD_LAG_MS)
    scalp_trader = ScalpPaperTrader(db=db) if settings.SCALP_ENABLED else None
    promoter     = PairPromoter(db=db, settings=settings)

    # ── Trader: paper / gate-testnet / live ───────────────────────────────────
    import os as _os
    _live_trading  = _os.getenv("LIVE_TRADING",  "false").lower() in ("1", "true", "yes")
    _gate_testnet  = _os.getenv("GATE_TESTNET",  "false").lower() in ("1", "true", "yes")

    if _live_trading:
        # ── FULL LIVE: real money on MEXC Futures + Gate Futures ──────────────
        _mexc_key    = _os.getenv("MEXC_FUTURES_API_KEY", "")
        _mexc_secret = _os.getenv("MEXC_FUTURES_SECRET", "")
        _gate_key    = _os.getenv("GATE_FUTURES_API_KEY", "")
        _gate_secret = _os.getenv("GATE_FUTURES_SECRET", "")
        if not all([_mexc_key, _mexc_secret, _gate_key, _gate_secret]):
            raise RuntimeError(
                "LIVE_TRADING=true requires MEXC_FUTURES_API_KEY, MEXC_FUTURES_SECRET, "
                "GATE_FUTURES_API_KEY, GATE_FUTURES_SECRET to be set"
            )
        _mexc_client = MexcFutures(_mexc_key, _mexc_secret)
        _gate_client = GateFutures(_gate_key, _gate_secret)
        await _mexc_client.__aenter__()
        await _gate_client.__aenter__()
        trader: PaperTrader | ArbLiveExecutor = ArbLiveExecutor(
            db=db,
            settings=settings,
            clients={"mexc": _mexc_client, "gate": _gate_client},
        )
        log.warning("🔴 LIVE TRADING ENABLED — real money on MEXC Futures + Gate Futures")

    elif _gate_testnet:
        # ── GATE TESTNET: real order execution on Gate testnet, MEXC is mocked ─
        _gate_key    = _os.getenv("GATE_FUTURES_TESTNET_API_KEY", "")
        _gate_secret = _os.getenv("GATE_FUTURES_TESTNET_SECRET", "")
        if not all([_gate_key, _gate_secret]):
            raise RuntimeError(
                "GATE_TESTNET=true requires GATE_FUTURES_TESTNET_API_KEY "
                "and GATE_FUTURES_TESTNET_SECRET to be set"
            )
        _gate_client = GateFutures(_gate_key, _gate_secret, testnet=True)
        _mock_client = MockFutures(name="mexc-mock")
        await _gate_client.__aenter__()
        await _mock_client.__aenter__()
        trader = ArbLiveExecutor(
            db=db,
            settings=settings,
            clients={"gate": _gate_client, "mexc": _mock_client},
        )
        log.warning(
            "🟡 GATE TESTNET MODE — real orders on Gate testnet, MEXC leg is mocked. "
            "Virtual funds only."
        )

    else:
        # ── PAPER TRADING (default) ────────────────────────────────────────────
        trader = PaperTrader(db=db, settings=settings)
        log.info("Paper trading mode (LIVE_TRADING not set)")

    # ── Compound: restore historical PnL so equity ratio survives restarts ────
    if hasattr(trader, 'update_equity_ratio') and db._pool:
        try:
            historical_pnl = await db.get_total_net_pnl()
            trader._total_net_pnl = historical_pnl
            trader.update_equity_ratio()
            log.info(
                "[Compound] Loaded historical PnL: +$%.4f → ratio=×%.3f",
                historical_pnl, trader._equity_ratio,
            )
        except Exception as exc:
            log.warning("[Compound] Failed to load historical PnL: %r", exc)

    # ── Flow tracker (tape + order book metrics for ML features) ──────────────
    flow_tracker    = FlowTracker()
    mexc_flow       = MexcFlowCollector(flow_tracker)
    gate_flow       = GateFlowCollector(flow_tracker)
    matrix.set_flow_tracker(flow_tracker)

    # Evaluate any symbols that accumulated data during previous session
    # ArbLiveExecutor has no evaluator — skip in live/testnet mode
    if hasattr(trader, 'evaluator'):
        await trader.evaluator.run_full_sweep()

    matrix.add_callback(trader.on_spread)

    if scalp_trader:
        # ScalpTrader startup: close ALL open positions from previous session (no age filter),
        # or full reset if SCALP_RESET=true. After any restart the bot has no memory of open
        # positions, so leaving them as 'open' forever causes duplicate entries per symbol.
        _scalp_reset = _os.getenv("SCALP_RESET", "").lower() in ("1", "true", "yes")
        await scalp_trader.startup(reset=_scalp_reset)
        if _scalp_reset:
            log.warning("[ScalpTrader] SCALP_RESET=true — fresh start, all old positions deleted.")
        matrix.add_callback(scalp_trader.on_spread)
    else:
        log.info("ScalpTrader disabled (SCALP_ENABLED=false)")

    # Push spread snapshots to the trading bot every 15s.
    # Uses internal Railway URL (no egress cost) when TRADING_BOT_URL_INTERNAL is set.
    # Increased from 5s → 15s: frontend polls every 30s anyway, 5s was wasting egress.
    matrix.set_push_url(
        url=f"{settings.internal_url}/api/arbitrage/internal/spread-update",
        interval_s=15,
    )

    # ── Collectors ────────────────────────────────────────────────────────────
    # Only instantiate collectors listed in ENABLED_COLLECTORS (default: gate,mexc).
    # Binance/Bybit are mark-price refs only — not needed for gate↔mexc arb.
    # KuCoin and MexcSpot disabled: consume WS connections with no trading value.
    _enabled = settings.enabled_collectors_set
    _all_collectors = {
        "gate":      GateCollector,
        "mexc":      MexcCollector,
        "binance":   BinanceCollector,
        "bybit":     BybitCollector,
        "kucoin":    KucoinCollector,
        "mexc_spot": MexcSpotCollector,
    }
    collectors = [cls() for name, cls in _all_collectors.items() if name in _enabled]
    log.info("Collectors enabled: %s", [c.name for c in collectors])
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

    # Start flow collectors (tape + book — MEXC and Gate only, no Binance/Bybit)
    flow_syms = [s for s in symbols if s.endswith("_USDT")]  # MEXC/Gate use underscore format
    try:
        await mexc_flow.connect(flow_syms)
        await gate_flow.connect(flow_syms)
        log.info("Flow collectors started for %d symbols", len(flow_syms))
    except Exception as exc:
        log.warning("Flow collectors failed to start: %r — continuing without flow data", exc)

    # ── Background loops ──────────────────────────────────────────────────────
    stats_push_url = f"{settings.internal_url}/api/arbitrage/internal/stats-update"

    async def report_loop() -> None:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(60)
                spreads = matrix.get_all_spreads()
                if hasattr(trader, 'update_equity_ratio'):
                    trader.update_equity_ratio()
                summary = trader.session_summary()

                log.info(
                    "━━ 60s report ━━  tracked=%d  open=%d  closed=%d  net_pnl=%+.4f USDT",
                    len(spreads),
                    summary["open_positions"],
                    summary["total_closed"],
                    summary["total_net_pnl"],
                )
                if hasattr(trader, '_equity_ratio'):
                    log.info(
                        "━━ equity  ━━  starting=$%.0f  equity=$%.2f  ratio=×%.3f  compound=%s",
                        settings.STARTING_EQUITY_USDT,
                        settings.STARTING_EQUITY_USDT + summary["total_net_pnl"],
                        trader._equity_ratio,
                        "ON" if settings.COMPOUND_ENABLED else "OFF",
                    )

                if scalp_trader:
                    scalp_summary = scalp_trader.session_summary()
                    log.info(
                        "━━ scalp      ━━  open=%d  closed=%d  net_pnl=%+.4f USDT",
                        scalp_summary["open_scalp"],
                        scalp_summary["total_closed"],
                        scalp_summary["total_net_pnl"],
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

                # Push scalp stats every 60s
                if scalp_trader:
                    try:
                        scalp_db_stats = await db.get_scalp_stats() if db._pool else {}
                        scalp_positions = await db.get_scalp_positions(limit=200) if db._pool else []
                        # Serialise datetime + Decimal objects (asyncpg returns NUMERIC as Decimal)
                        from decimal import Decimal
                        for p in scalp_positions:
                            for k, v in list(p.items()):
                                if hasattr(v, "isoformat"):
                                    p[k] = v.isoformat()
                                elif isinstance(v, Decimal):
                                    p[k] = float(v)
                        scalp_payload = {
                            "stats":     scalp_db_stats,
                            "session":   scalp_summary,
                            "positions": scalp_positions,
                        }
                        async with session.post(
                            f"{settings.internal_url}/api/scalp/internal/stats-update",
                            json=scalp_payload,
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as resp:
                            if resp.status >= 400:
                                log.warning("[ScalpStats push] HTTP %d", resp.status)
                    except Exception as exc:
                        log.warning("[ScalpStats push] Error: %r", exc)

                # Push symbol lifecycle states every 60s
                try:
                    sym_states = await db.get_all_symbol_states()
                    # Convert datetime objects to ISO strings for JSON serialisation
                    for s in sym_states:
                        for k, v in s.items():
                            if hasattr(v, "isoformat"):
                                s[k] = v.isoformat()
                    async with session.post(
                        f"{settings.internal_url}/api/arbitrage/internal/symbol-states-update",
                        json=sym_states,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status >= 400:
                            log.warning("[SymStates push] HTTP %d", resp.status)
                except Exception as exc:
                    log.debug("[SymStates push] Error: %r", exc)

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
