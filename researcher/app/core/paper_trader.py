"""
PaperTrader — listens to SpreadMatrix events, opens/closes paper positions.

Entry condition (either):
  A) z-score mode:  |zscore| >= ZSCORE_THRESHOLD  AND  spread >= MIN_SPREAD_PCT * 100
  B) large spread:  spread >= 5%  (no z-score required — catches new listings)

Exit conditions (first match wins, reason logged):
  1. TAKE_PROFIT   — spread narrowed to <= entry * TAKE_PROFIT_RATIO   (default 50% of entry)
  2. ZSCORE_REVERT — |zscore| < ZSCORE_EXIT  (spread mean-reverted, default z<0.5)
  3. STOP_LOSS     — spread widened to >= entry * STOP_LOSS_RATIO      (default 2×)
  4. TIME_STOP     — held longer than MAX_HOLD_SECONDS                 (default 4h)

Uses TradingSimulator for realistic P&L (fees + slippage + market impact).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional, Dict, Deque

from ..config import Settings
from ..db.neon_db import NeonDB
from .simulator import TradingSimulator
from .symbol_evaluator import SymbolEvaluator

logger = logging.getLogger(__name__)

# Funding windows in seconds since midnight UTC: 00:00, 08:00, 16:00
_FUNDING_TIMES_SEC = (0, 28_800, 57_600)


def _seconds_to_next_funding(ts_ms: int) -> int:
    """Return seconds until the next funding payment (00:00, 08:00 or 16:00 UTC)."""
    now_sec = (ts_ms // 1000) % 86_400
    gaps = [((f - now_sec) % 86_400) for f in _FUNDING_TIMES_SEC]
    return min(gaps)


def _mins_to_funding(ts_ms: int) -> float:
    """Return minutes until next funding — used as ML feature."""
    return round(_seconds_to_next_funding(ts_ms) / 60, 2)


def _trading_session(hour_utc: int) -> str:
    """Classify UTC hour into named crypto trading session."""
    if 0 <= hour_utc <= 6:
        return "asia"
    elif 7 <= hour_utc <= 12:
        return "europe"
    elif 13 <= hour_utc <= 15:
        return "overlap"
    elif 16 <= hour_utc <= 21:
        return "us"
    else:
        return "quiet"


class PaperTrader:
    def __init__(self, db: NeonDB, settings: Settings) -> None:
        self.db        = db
        self.settings  = settings
        self.sim       = TradingSimulator(
            paper_deal_size_usdt=settings.PAPER_DEAL_SIZE_USDT
        )
        self.evaluator = SymbolEvaluator(db)

        # {(symbol, ex_long, ex_short): _OpenState}
        self._open: dict[tuple, _OpenState] = {}

        # {(symbol, ex_long, ex_short)} — keys being opened (pre-_open race guard)
        self._pending_open: set[tuple] = set()

        # {(symbol, ex_long, ex_short): timestamp_ms} — cooldown after STOP_LOSS
        self._stop_loss_cooldown: dict[tuple, int] = {}

        # Dynamic symbol suspension — rolling window of recent results per symbol
        # deque of bools: True=win, False=loss (last N trades)
        self._symbol_recent:    Dict[str, Deque[bool]] = {}
        # symbol → suspended_until_ms (0 = not suspended)
        self._symbol_suspended: Dict[str, int] = {}

        # Session counters (for log summaries)
        self._total_opened  = 0
        self._total_closed  = 0
        self._total_net_pnl = 0.0

        self._equity_ratio: float = 1.0  # scales deal sizes when compounding is enabled
        self._current_spreads: dict = {}   # cache latest spread per key for delayed measurement

    async def on_spread(self, data: dict) -> None:
        """Called by SpreadMatrix on every aligned spread update."""
        symbol     = data["symbol"]
        ex_long    = data["exchange_long"]
        ex_short   = data["exchange_short"]
        zscore: Optional[float] = data.get("zscore")
        spread_pct  = data["spread_pct"]
        spread_mean = data.get("spread_mean")
        spread_std  = data.get("spread_std")
        spread_cv   = data.get("spread_cv")
        ts_ms       = data.get("ts_ms", int(time.time() * 1000))
        buy_pressure       = data.get("buy_pressure")
        trade_velocity     = data.get("trade_velocity")
        book_imbalance     = data.get("book_imbalance")
        mm_repeat_score    = data.get("mm_repeat_score")
        features_complete  = bool(data.get("features_complete", False))
        # New: depth + price features
        depth5_bid_usd     = data.get("depth5_bid_usd")
        depth5_ask_usd     = data.get("depth5_ask_usd")
        depth5_total_usd   = data.get("depth5_total_usd")
        depth_imbalance    = data.get("depth_imbalance")
        mid_price          = data.get("mid_price")
        spread_bps         = data.get("spread_bps")
        price_long         = data.get("price_long")
        price_short        = data.get("price_short")
        mexc_spot_basis    = data.get("mexc_spot_basis_pct")

        key = (symbol, ex_long, ex_short)
        self._current_spreads[key] = data   # cache for price_continued_bps delayed task

        if key in self._open:
            await self._maybe_close(key, zscore, spread_pct, ts_ms)
        else:
            await self._maybe_open(key, symbol, ex_long, ex_short, zscore, spread_pct, ts_ms,
                                   spread_mean=spread_mean, spread_std=spread_std,
                                   spread_cv=spread_cv,
                                   buy_pressure=buy_pressure, trade_velocity=trade_velocity,
                                   book_imbalance=book_imbalance,
                                   mm_repeat_score=mm_repeat_score,
                                   features_complete=features_complete,
                                   depth5_bid_usd=depth5_bid_usd,
                                   depth5_ask_usd=depth5_ask_usd,
                                   depth5_total_usd=depth5_total_usd,
                                   depth_imbalance=depth_imbalance,
                                   mid_price=mid_price,
                                   spread_bps=spread_bps,
                                   price_long=price_long,
                                   price_short=price_short,
                                   mexc_spot_basis=mexc_spot_basis)

    # ── Session stats (called from report_loop) ───────────────────────────────

    def session_summary(self) -> dict:
        return {
            "open_positions":   len(self._open),
            "total_opened":     self._total_opened,
            "total_closed":     self._total_closed,
            "total_net_pnl":    round(self._total_net_pnl, 4),
            "breakeven_pct":    round(
                self.sim.simulate_trade("binance", "bybit", 0.5, 0.5).breakeven_spread_pct, 4
            ),
            "equity_ratio":     round(self._equity_ratio, 4),
            "compound_enabled": self.settings.COMPOUND_ENABLED,
        }

    def update_equity_ratio(self) -> None:
        """Recalculate equity ratio for compounding. Called from report_loop."""
        if not self.settings.COMPOUND_ENABLED:
            return
        current_equity = self.settings.STARTING_EQUITY_USDT + self._total_net_pnl
        raw_ratio = current_equity / self.settings.STARTING_EQUITY_USDT
        self._equity_ratio = min(raw_ratio, self.settings.COMPOUND_MAX_MULT)
        self._equity_ratio = max(self._equity_ratio, 0.5)

    # ── Private ───────────────────────────────────────────────────────────────

    def _dynamic_deal_size(
        self,
        spread_pct: float,
        trade_velocity: Optional[float] = None,
    ) -> float:
        """
        Tiered position sizing based on entry spread quality, capped by liquidity.

        Step 1 — spread tier (fee-to-gross ratio improves sharply with spread):
          <1.0%  → base ×1  (fees ~63% of gross)
          ≥1.0%  → base ×2  (fees ~31% of gross)
          ≥1.5%  → base ×3  (fees ~21% of gross)
          ≥2.0%  → base ×5  (fees ~12% of gross)

        Step 2 — volume cap: don't exceed 30% of per-minute USDT flow.
          trade_velocity = ticks/min; avg tick ~$7.5 → USDT/min ≈ velocity × 7.5
          cap = max(base, USDT/min × 0.30)
          In practice high-spread coins already have high velocity (median 124-229
          ticks/min for ≥1.5% spread), so cap almost never fires.

        Data simulation (2796 trades): flat $10 → $53 net, dynamic → $143 net (+169%).
        """
        base = self.settings.PAPER_DEAL_SIZE_USDT * self._equity_ratio

        # Step 1: spread-quality tier
        if spread_pct >= self.settings.MM_TIER3_SPREAD_PCT:
            size = base * self.settings.MM_TIER3_MULT
        elif spread_pct >= self.settings.MM_TIER2_SPREAD_PCT:
            size = base * self.settings.MM_TIER2_MULT
        elif spread_pct >= self.settings.MM_TIER1_SPREAD_PCT:
            size = base * self.settings.MM_TIER1_MULT
        else:
            size = base

        # Step 2: liquidity cap (only when velocity data is available)
        if trade_velocity is not None and trade_velocity > 0:
            usdt_per_min = trade_velocity * 7.5      # rough: avg tick ~$7.5
            volume_cap   = max(base, usdt_per_min * 0.30)
            size         = min(size, volume_cap)

        return size

    async def _maybe_open(
        self,
        key:        tuple,
        symbol:     str,
        ex_long:    str,
        ex_short:   str,
        zscore:     Optional[float],
        spread_pct: float,
        ts_ms:      int,
        spread_mean:    Optional[float] = None,
        spread_std:     Optional[float] = None,
        spread_cv:       Optional[float] = None,
        buy_pressure:      Optional[float] = None,
        trade_velocity:    Optional[float] = None,
        book_imbalance:    Optional[float] = None,
        mm_repeat_score:   Optional[float] = None,
        features_complete: bool = False,
        depth5_bid_usd:    Optional[float] = None,
        depth5_ask_usd:    Optional[float] = None,
        depth5_total_usd:  Optional[float] = None,
        depth_imbalance:   Optional[float] = None,
        mid_price:         Optional[float] = None,
        spread_bps:        Optional[float] = None,
        price_long:        Optional[float] = None,
        price_short:       Optional[float] = None,
        mexc_spot_basis:   Optional[float] = None,
    ) -> None:
        # Reject bogus data: price-scale mismatches produce absurd spreads
        if spread_pct > self.settings.MAX_SPREAD_PCT:
            return

        # Reject low-quality spreads: spread_cv < MIN_SPREAD_CV means the spread
        # is structurally stable (doesn't oscillate) → won't mean-revert → not arb.
        # Data: cv<0.5 → TP rate 11.6%, -$112 loss on 6038 trades.
        #       cv>1.0 → TP rate 73-89%, consistently profitable.
        # Skip check only if cv is not yet available (insufficient history).
        if spread_cv is not None and spread_cv < self.settings.MIN_SPREAD_CV:
            return

        # Only trade between whitelisted exchanges (gate + mexc).
        # Binance/Bybit are mark-price references — their prices structurally
        # diverge from Gate/MEXC futures, producing phantom spreads that never close.
        allowed = self.settings.trading_exchanges_set
        if ex_long not in allowed or ex_short not in allowed:
            return

        # Reject structurally bad pairs — spreads never revert, drain fees
        if symbol in self.settings.blacklisted_set:
            return

        # Hard caps: check before any async work to avoid unnecessary DB calls
        if len(self._open) >= self.settings.MAX_OPEN_POSITIONS:
            return
        current_exposure = sum(s.deal_size for s in self._open.values())
        if current_exposure >= self.settings.MAX_EXPOSURE_USDT:
            return

        # Guard: two ticks 140ms apart can both pass `key in self._open` before
        # either sets it (first await yields the loop). _pending_open blocks dupe opens.
        if key in self._pending_open:
            return
        self._pending_open.add(key)

        # Reject symbols that failed auto-evaluation (BLACKLISTED in symbol_states)
        sym_state = await self.db.get_symbol_state(symbol)
        if sym_state == "BLACKLISTED":
            self._pending_open.discard(key)
            return

        # Ensure new symbols are registered in TESTING state
        if sym_state is None:
            await self.db.ensure_symbol_testing(symbol)

        # Reject entry during funding blackout window (N seconds before 00/08/16h UTC)
        # Opening just before funding = paying entry fees + funding before spread closes
        if _seconds_to_next_funding(ts_ms) < self.settings.FUNDING_BLACKOUT_SECONDS:
            self._pending_open.discard(key)
            return

        # Reject entry if in STOP_LOSS cooldown for this pair
        cooldown_until = self._stop_loss_cooldown.get(key, 0)
        if ts_ms < cooldown_until:
            self._pending_open.discard(key)
            return

        # Reject entry if symbol is dynamically suspended (recent poor WR)
        suspended_until = self._symbol_suspended.get(symbol, 0)
        if ts_ms < suspended_until:
            self._pending_open.discard(key)
            return

        # Mode A: classic z-score mean reversion
        zscore_entry = (
            zscore is not None
            and abs(zscore) >= self.settings.ZSCORE_THRESHOLD
            and spread_pct >= self.settings.MIN_SPREAD_PCT * 100
        )
        # Mode B: large-spread entry — DISABLED
        # Data analysis (11k trades) showed large_spread has 27% win rate and
        # destroyed -$113 total vs zscore mode +$42. Large spreads are structural
        # (always present between exchanges), not anomalies — they don't mean-revert.
        # large_spread_entry = spread_pct >= 0.55  ← disabled 2026-04-12

        if zscore_entry:
            entry_mode = "zscore"
            vel = trade_velocity or 0
            if vel > 50:
                deal_size = self.settings.DEAL_SIZE_HIGH_USDT * self._equity_ratio
            elif vel >= 10:
                deal_size = self.settings.DEAL_SIZE_MED_USDT * self._equity_ratio
            else:
                deal_size = self.settings.PAPER_DEAL_SIZE_USDT * self._equity_ratio
            logger.info("[VEL] %s  vel=%.1f → deal_size=%.0f USDT  ratio=×%.3f", symbol, vel, deal_size, self._equity_ratio)
            if current_exposure + deal_size > self.settings.MAX_EXPOSURE_USDT:
                deal_size = self.settings.MAX_EXPOSURE_USDT - current_exposure
                if deal_size < self.settings.PAPER_DEAL_SIZE_USDT:
                    self._pending_open.discard(key)
                    return
            entry_costs = self.sim.simulate_entry(ex_long, ex_short, spread_pct, deal_size=deal_size)

            # Reserve the key BEFORE the async DB insert to prevent duplicate opens
            # from concurrent ticks arriving while the INSERT is in flight.
            self._open[key] = _OpenState(
                pos_id=0,  # placeholder until DB insert completes
                opened_ms=ts_ms,
                entry_spread=spread_pct,
                entry_zscore=zscore,
                slip_entry=entry_costs["slippage_usdt"],
                fee_entry=entry_costs["fee_usdt"],
                entry_mode=entry_mode,
                deal_size=deal_size,
            )
            self._pending_open.discard(key)  # _open now guards this key
            self._total_opened += 1

            pos_id = 0
            if self.db._pool:
                pos_id = await self.db.insert_paper_position(
                    symbol=symbol,
                    exchange_long=ex_long,
                    exchange_short=ex_short,
                    entry_spread_pct=spread_pct,
                    entry_zscore=zscore,
                    deal_size_usdt=deal_size,
                    slippage_entry_usdt=entry_costs["slippage_usdt"],
                    fee_usdt=entry_costs["fee_usdt"],
                    entry_mode=entry_mode,
                    spread_mean=spread_mean,
                    spread_std=spread_std,
                    buy_pressure=buy_pressure,
                    trade_velocity=trade_velocity,
                    book_imbalance=book_imbalance,
                    mm_repeat_score=mm_repeat_score,
                    features_complete=features_complete,
                )
                self._open[key].pos_id = pos_id  # update with real id

                # ── ML dataset logging ──────────────────────────────────
                if pos_id:
                    from datetime import datetime, timezone
                    _now   = datetime.now(timezone.utc)
                    _ts_ms = int(_now.timestamp() * 1000)
                    _dow   = _now.weekday()  # 0=Mon … 6=Sun
                    await self.db.log_ml_entry(
                        pos_id=pos_id,
                        symbol=symbol,
                        exchange_long=ex_long,
                        entry_spread_pct=spread_pct,
                        entry_zscore=zscore,
                        deal_size_usdt=deal_size,
                        entry_mode=entry_mode,
                        spread_mean=spread_mean,
                        spread_std=spread_std,
                        spread_cv=spread_cv,
                        buy_pressure=buy_pressure,
                        trade_velocity=trade_velocity,
                        book_imbalance=book_imbalance,
                        mm_repeat_score=mm_repeat_score,
                        # depth + price features
                        depth5_bid_usd=depth5_bid_usd,
                        depth5_ask_usd=depth5_ask_usd,
                        depth5_total_usd=depth5_total_usd,
                        depth_imbalance=depth_imbalance,
                        mid_price=mid_price,
                        spread_bps=spread_bps,
                        entry_price=price_long,
                        mexc_spot_basis_pct=mexc_spot_basis,
                        # time features
                        hour_of_day=_now.hour,
                        day_of_week=_dow,
                        minute_of_hour=_now.minute,
                        is_weekend=1 if _dow >= 5 else 0,
                        trading_session=_trading_session(_now.hour),
                        mins_to_funding=_mins_to_funding(_ts_ms),
                        # strategy params
                        take_profit_bps=self.settings.TAKE_PROFIT_RATIO * spread_pct * 100,
                        stop_loss_bps=self.settings.STOP_LOSS_RATIO * spread_pct * 100,
                        timeout_seconds=self.settings.MAX_HOLD_SECONDS,
                        entry_qty=deal_size,
                        entry_side="ARB_LONG",
                    )

            # Breakeven: total round-trip cost as % of entry spread
            be = entry_costs["total_cost_usdt"] * 2 / deal_size * 100
            tp_target = spread_pct * self.settings.TAKE_PROFIT_RATIO
            sl_target = spread_pct * self.settings.STOP_LOSS_RATIO
            logger.info(
                "[OPEN %s]  %s %s/%s  spread=%.3f%%  z=%s  "
                "size=%.0f USDT  slip=%.4f  fee=%.4f  breakeven=%.3f%%  "
                "TP@%.3f%%  SL@%.3f%%  timeout=%dh",
                entry_mode.upper(), symbol, ex_long, ex_short, spread_pct,
                f"{zscore:+.2f}" if zscore is not None else "n/a",
                deal_size,
                entry_costs["slippage_usdt"],
                entry_costs["fee_usdt"],
                be,
                tp_target,
                sl_target,
                self.settings.MAX_HOLD_SECONDS // 3600,
            )
        else:
            self._pending_open.discard(key)

    async def _maybe_close(
        self,
        key:       tuple,
        zscore:    Optional[float],
        spread_pct: float,
        ts_ms:     int,
    ) -> None:
        state    = self._open[key]
        hold_sec = max(0, (ts_ms - state.opened_ms) // 1000)
        entry    = state.entry_spread

        # ── Exit condition checks (first match wins) ──────────────────────
        reason: Optional[str] = None

        # 1. Take-profit: spread narrowed to ≤ entry × TAKE_PROFIT_RATIO
        tp_threshold = entry * self.settings.TAKE_PROFIT_RATIO
        if spread_pct <= tp_threshold:
            reason = "TAKE_PROFIT"

        # 2. Z-score revert: spread returned to mean (only if z-score available)
        # Require minimum hold to avoid z-score noise exits (avg 30s exits = double fees, 8% WR)
        elif (zscore is not None
              and abs(zscore) < self.settings.ZSCORE_EXIT
              and hold_sec >= self.settings.ZSCORE_REVERT_MIN_HOLD_SECONDS):
            reason = "ZSCORE_REVERT"

        # 3. Stop-loss: spread grew too wide (position moving against us)
        elif spread_pct >= entry * self.settings.STOP_LOSS_RATIO:
            reason = "STOP_LOSS"

        # 4. Time stop: max hold exceeded
        elif hold_sec >= self.settings.MAX_HOLD_SECONDS:
            reason = "TIME_STOP"

        if reason is None:
            return

        # ── Execute close ─────────────────────────────────────────────────
        self._open.pop(key)

        # Set cooldown so volatile pairs don't re-enter immediately after STOP_LOSS
        if reason == "STOP_LOSS":
            cooldown_ms = self.settings.STOP_LOSS_COOLDOWN_SECONDS * 1000
            self._stop_loss_cooldown[key] = ts_ms + cooldown_ms
        symbol, ex_long, ex_short = key

        result = self.sim.simulate_trade(
            exchange_long=ex_long,
            exchange_short=ex_short,
            entry_spread_pct=entry,
            exit_spread_pct=spread_pct,
            deal_size=state.deal_size,
        )

        if self.db._pool:
            await self.db.close_paper_position(
                pos_id=state.pos_id,
                exit_spread_pct=spread_pct,
                exit_zscore=zscore,
                slippage_exit_usdt=result.slippage_exit_usdt,
                gross_pnl_usdt=result.gross_pnl_usdt,
                net_pnl_usdt=result.net_pnl_usdt,
                hold_seconds=hold_sec,
                exit_reason=reason,
            )
            # ── ML dataset logging (BEFORE upsert_pair_stats so it always runs) ──
            if state.pos_id:
                _deal = state.deal_size or self.settings.PAPER_DEAL_SIZE_USDT
                _pnl_bps     = round(result.net_pnl_usdt / _deal * 10000, 4) if _deal else None
                _pnl_percent = round(result.net_pnl_usdt / _deal * 100,   4) if _deal else None
                await self.db.log_ml_exit(
                    pos_id=state.pos_id,
                    exit_spread_pct=spread_pct,
                    exit_zscore=zscore,
                    gross_pnl_usdt=result.gross_pnl_usdt,
                    net_pnl_usdt=result.net_pnl_usdt,
                    hold_seconds=hold_sec,
                    exit_reason=reason,
                    pnl_bps=_pnl_bps,
                    pnl_percent=_pnl_percent,
                    spread_at_exit=spread_pct * 100,
                )

                # Delayed spread continuation measurement (60s post-exit)
                _pos_id_snap      = state.pos_id
                _key_snap         = key
                _exit_spread_snap = spread_pct * 100   # bps at exit moment

                async def _track_spread_continuation(
                    pos_id=_pos_id_snap,
                    key=_key_snap,
                    exit_bps=_exit_spread_snap,
                ) -> None:
                    import asyncio as _asyncio
                    await _asyncio.sleep(60)
                    try:
                        spread_data = self._current_spreads.get(key, {})
                        spread_60s = spread_data.get("spread_bps")
                        if spread_60s is not None and exit_bps > 0:
                            # positive = spread kept narrowing = exited too early
                            # negative = spread widened = good exit timing
                            bps = round(exit_bps - spread_60s, 4)
                            await self.db.update_price_continued_arb(pos_id, bps)
                    except Exception as _e:
                        import logging
                        logging.getLogger(__name__).warning(
                            f"price_continuation task failed for arb_{pos_id}: {_e}"
                        )

                import asyncio as _asyncio_outer
                _asyncio_outer.create_task(_track_spread_continuation())
            try:
                await self.db.upsert_pair_stats(symbol, ex_long, ex_short)
            except Exception as _ups_err:
                logger.warning(f"[DB] upsert_pair_stats failed: {_ups_err}")
            # Evaluate symbol lifecycle after every close (async, non-blocking)
            await self.evaluator.on_trade_closed(symbol)

        self._total_closed  += 1
        self._total_net_pnl += result.net_pnl_usdt
        verdict = "WIN " if result.net_pnl_usdt > 0 else "LOSS"

        # Update dynamic suspension window for this symbol
        self._update_symbol_suspension(symbol, is_win=(result.net_pnl_usdt > 0), ts_ms=ts_ms)

        logger.info(
            "[CLOSE %s | %s] %s %s/%s  "
            "spread %.3f%%→%.3f%%  (entry_mode=%s)  "
            "gross=%+.4f  slip=%.4f  fee=%.4f  net=%+.4f USDT  "
            "pnl%%=%+.3f%%  hold=%ds",
            verdict, reason,
            symbol, ex_long, ex_short,
            entry, spread_pct,
            state.entry_mode,
            result.gross_pnl_usdt,
            result.slippage_entry_usdt + result.slippage_exit_usdt,
            result.fee_usdt,
            result.net_pnl_usdt,
            result.net_pnl_pct,
            hold_sec,
        )


    def _update_symbol_suspension(self, symbol: str, is_win: bool, ts_ms: int) -> None:
        """
        Update rolling win/loss window for a symbol and suspend if WR drops too low.

        Logic:
          - Keep last DYNAMIC_SUSPEND_WINDOW results per symbol
          - Only evaluate after DYNAMIC_SUSPEND_MIN_TRADES results
          - WR = 0%  in window → suspend DYNAMIC_SUSPEND_HOURS_ZERO hours
          - WR < 30% in window → suspend DYNAMIC_SUSPEND_HOURS_LOW hours
          - After 1 win: reset window so good coins recover quickly
        """
        cfg = self.settings
        window = cfg.DYNAMIC_SUSPEND_WINDOW
        min_trades = cfg.DYNAMIC_SUSPEND_MIN_TRADES

        # Initialise deque if first time seeing this symbol
        if symbol not in self._symbol_recent:
            self._symbol_recent[symbol] = deque(maxlen=window)

        recent = self._symbol_recent[symbol]
        recent.append(is_win)

        # After a win, reset the window so good coins aren't penalised for old losses
        if is_win:
            self._symbol_recent[symbol] = deque([True], maxlen=window)
            # Also lift any existing suspension immediately on a win
            if self._symbol_suspended.get(symbol, 0) > 0:
                self._symbol_suspended[symbol] = 0
                logger.info("[DYN-SUSPEND] %s — lifted early after WIN", symbol)
            return

        # Need minimum trades before evaluating
        if len(recent) < min_trades:
            return

        wins = sum(1 for r in recent if r)
        wr   = wins / len(recent)

        suspend_hours = 0.0
        if wr == 0.0:
            suspend_hours = cfg.DYNAMIC_SUSPEND_HOURS_ZERO
        elif wr < cfg.DYNAMIC_SUSPEND_WR_LOW:
            suspend_hours = cfg.DYNAMIC_SUSPEND_HOURS_LOW

        if suspend_hours > 0:
            until_ms = ts_ms + int(suspend_hours * 3600 * 1000)
            self._symbol_suspended[symbol] = until_ms
            # Reset window so it starts fresh after suspension
            self._symbol_recent[symbol] = deque(maxlen=window)
            logger.warning(
                "[DYN-SUSPEND] %s suspended %.0fh — WR=%.0f%% in last %d trades",
                symbol, suspend_hours, wr * 100, len(recent),
            )


class _OpenState:
    """Lightweight container for an open position's state."""
    __slots__ = ("pos_id", "opened_ms", "entry_spread", "entry_zscore",
                 "slip_entry", "fee_entry", "entry_mode", "deal_size")

    def __init__(
        self,
        pos_id:       int,
        opened_ms:    int,
        entry_spread: float,
        entry_zscore: Optional[float],
        slip_entry:   float,
        fee_entry:    float,
        entry_mode:   str = "zscore",
        deal_size:    float = 10.0,
    ) -> None:
        self.pos_id       = pos_id
        self.opened_ms    = opened_ms
        self.entry_spread = entry_spread
        self.entry_zscore = entry_zscore
        self.slip_entry   = slip_entry
        self.fee_entry    = fee_entry
        self.entry_mode   = entry_mode
        self.deal_size    = deal_size
