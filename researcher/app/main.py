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

    # ── Symbols — loaded from NeonDB → file → fresh discovery → env fallback ──
    symbols: list[str] = []

    # Priority 1: NeonDB (persists across Railway restarts)
    if db._pool:
        try:
            raw = await db.load_bot_config("discovered_symbols")
            if raw:
                symbols = json.loads(raw)
                log.info("[Symbols] Loaded %d symbols from NeonDB cache", len(symbols))
        except Exception as exc:
            log.warning("[Symbols] NeonDB load failed: %r", exc)

    # Priority 2: local file (useful in local dev)
    if not symbols:
        symbols_file = Path(settings.SYMBOLS_FILE)
        if symbols_file.exists():
            try:
                discovered = json.loads(symbols_file.read_text())
                symbols = discovered["symbols"]
                new_count = len(discovered.get("new_listings", []))
                log.info(
                    "[Symbols] Loaded %d symbols from file %s (%d new listings)",
                    len(symbols), symbols_file, new_count,
                )
                if db._pool:
                    await db.save_bot_config("discovered_symbols", json.dumps(symbols))
                    log.info("[Symbols] File list persisted to NeonDB")
            except Exception as exc:
                log.warning("[Symbols] File load failed: %r", exc)

    # Priority 3: fresh discovery
    if not symbols:
        log.info("[Symbols] No cache found — running live discovery…")
        try:
            symbols = await discover_symbols(save=True)
            log.info("[Symbols] Discovery found %d symbols", len(symbols))
            if db._pool:
                await db.save_bot_config("discovered_symbols", json.dumps(symbols))
                log.info("[Symbols] Discovery result persisted to NeonDB")
        except Exception as exc:
            log.warning("[Symbols] Discovery failed: %r — using env fallback", exc)

    # Priority 3.5: historical symbols from symbol_states (proven arb candidates)
    # Triggered when discovery returns only fallback symbols (≤ 5) or fails entirely
    if len(symbols) <= 5 and db._pool:
        try:
            historical = await db.get_active_symbols()
            if len(historical) > 5:
                symbols = historical
                log.info(
                    "[Symbols] Loaded %d historical symbols from symbol_states DB "
                    "(discovery returned only fallback symbols)",
                    len(symbols),
                )
                await db.save_bot_config("discovered_symbols", json.dumps(symbols))
            else:
                log.warning("[Symbols] symbol_states has only %d non-blacklisted symbols", len(historical))
        except Exception as exc:
            log.warning("[Symbols] Historical symbols load failed: %r", exc)

    # Priority 4: env var fallback (only BTC/ETH/SOL etc — not ideal)
    if not symbols:
        symbols = settings.symbols_list
        log.warning("[Symbols] Using env var fallback: %d symbols only", len(symbols))

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

    # ── Contract specs: size multipliers for correct book/tape USD notional ────
    # Gate quanto_multiplier / MEXC contractSize vary per contract (0.0001…10000);
    # without them depth5_*_usd is wrong. Fetched once here, before books arrive.
    from .core.contract_specs import ContractSpecs
    contract_specs = ContractSpecs()
    try:
        await contract_specs.load()
        flow_tracker.set_specs(contract_specs)
        log.info("[ContractSpecs] %d multipliers cached (book/tape USD now correct)", len(contract_specs))
    except Exception as exc:
        log.warning("[ContractSpecs] load failed: %r — depth USD reported None until next restart", exc)

    # ── Book-aware execution (Step C): price paper P&L against real books ──────
    # PaperTrader walks the live book (VWAP + depth) for executable entry/exit
    # fills. ArbLiveExecutor (live/testnet) has no such method — guard with hasattr.
    if hasattr(trader, "set_flow_tracker"):
        trader.set_flow_tracker(flow_tracker)
        log.info("[Exec] PaperTrader wired to FlowTracker — P&L priced on real books (sim_priced=exec)")

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
                if "exec_entry_rejects" in summary:
                    _op = summary["total_opened"]
                    _rj = summary["exec_entry_rejects"]
                    _denom = _op + _rj
                    log.info(
                        "━━ exec gate  ━━  opened=%d  entry_rejects=%d  reject_rate=%.1f%%  exit_defers=%d",
                        _op, _rj,
                        (100.0 * _rj / _denom) if _denom else 0.0,
                        summary["exec_exit_defers"],
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

            # Persist refreshed list to NeonDB so next restart doesn't lose it
            if db._pool:
                try:
                    await db.save_bot_config("discovered_symbols", json.dumps(new_symbols))
                    log.info("[Watcher] Refreshed symbol list persisted to NeonDB (%d symbols)", len(new_symbols))
                except Exception as exc:
                    log.warning("[Watcher] Failed to persist symbols to NeonDB: %r", exc)

    # Make symbols mutable for hot reload
    symbols = list(symbols)

    async def spread_observation_loop() -> None:
        """MEASUREMENT ONLY — sample the executable (book-crossing) spread for ALL
        notable ticks (mark ≥ 0.3%, NO upper cap) so we can test whether any
        big-divergence taker regime is executable-positive. Reads books via
        FlowTracker (top-of-book best bid/ask, consistent with Steps A+B) and
        batch-inserts into spread_observations. Enters NO trades, touches NO P&L.
        """
        SAMPLE_INTERVAL = 15.0
        MIN_MARK_BPS    = 30.0     # 0.3% — we want the big divergences too
        STALE_MS        = 2000
        z_thr    = settings.ZSCORE_THRESHOLD
        cv_min   = settings.MIN_SPREAD_CV
        max_bps  = settings.MAX_SPREAD_PCT * 100.0   # 50% → 5000 bps
        allowed  = settings.trading_exchanges_set    # only pairs with real books both sides
        while True:
            await asyncio.sleep(SAMPLE_INTERVAL)
            if not db._pool:
                continue
            try:
                rows = []
                for s in matrix.get_all_spreads():
                    mark_bps = s.get("spread_bps")
                    if mark_bps is None or mark_bps < MIN_MARK_BPS:
                        continue
                    ex_long  = s["exchange_long"]
                    ex_short = s["exchange_short"]
                    if ex_long not in allowed or ex_short not in allowed:
                        continue   # binance/bybit are mark-only refs — no book to cross
                    symbol = s["symbol"]
                    mid    = s.get("mid_price") or 0.0

                    bba_long  = flow_tracker.get_best_bid_ask(symbol, ex_long)
                    bba_short = flow_tracker.get_best_bid_ask(symbol, ex_short)
                    age_long  = flow_tracker.get_book_age_ms(symbol, ex_long)
                    age_short = flow_tracker.get_book_age_ms(symbol, ex_short)
                    book_fresh = (
                        bba_long is not None and bba_short is not None
                        and age_long is not None and age_short is not None
                        and age_long <= STALE_MS and age_short <= STALE_MS
                    )
                    executable = cross_cost = None
                    if book_fresh and mid > 0:
                        ask_long  = bba_long[1]    # long leg pays the ask
                        bid_short = bba_short[0]   # short leg hits the bid
                        executable = round((bid_short - ask_long) / mid * 10000, 4)
                        cross_cost = round(mark_bps - executable, 4)

                    dl_bid, dl_ask = flow_tracker.get_depth_usd(symbol, ex_long)
                    ds_bid, ds_ask = flow_tracker.get_depth_usd(symbol, ex_short)
                    depth_long  = ((dl_bid or 0.0) + (dl_ask or 0.0)) or None
                    depth_short = ((ds_bid or 0.0) + (ds_ask or 0.0)) or None

                    z  = s.get("zscore")
                    cv = s.get("spread_cv")
                    entered_eligible = bool(
                        z is not None and abs(z) >= z_thr
                        and cv is not None and cv >= cv_min
                        and mark_bps <= max_bps
                    )
                    rows.append({
                        "symbol": symbol, "exchange_long": ex_long, "exchange_short": ex_short,
                        "mark_spread_bps": round(mark_bps, 4),
                        "executable_spread_bps": executable,
                        "exec_vs_mark_edge_bps": cross_cost,
                        "zscore": z, "spread_cv": cv,
                        "depth5_long_usd": depth_long, "depth5_short_usd": depth_short,
                        "book_fresh": book_fresh, "entered_eligible": entered_eligible,
                    })
                if rows:
                    n = await db.insert_spread_observations(rows)
                    log.info("[SpreadObs] sampled %d notable pairs (≥%.0fbps mark) → logged %d",
                             len(rows), MIN_MARK_BPS, n)
            except Exception as exc:
                log.warning("[SpreadObs] sampler cycle failed: %r", exc)

    await asyncio.gather(
        report_loop(),
        matrix.push_loop(),
        promoter.run(),
        symbol_reload_loop(),   # 24h hot reload — no restart needed
        spread_observation_loop(),   # measurement-only executable-spread sampler
    )


if __name__ == "__main__":
    asyncio.run(main())
