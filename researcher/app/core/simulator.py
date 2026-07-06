"""
TradingSimulator — realistic PnL modelling for paper cross-exchange arb.

Accounts for:
  - Per-exchange taker fees
  - Base slippage (per exchange) + market-impact component
  - 4-leg round-trip cost (long entry, short entry, long exit, short exit)
  - Leverage-aware position sizing with margin buffer
  - Drawdown guard: stops new positions when account drawdown exceeds threshold
  - Sharpe ratio (annualised) and max drawdown utilities

Leverage notes (based on research):
  - Cross-exchange arb is delta-neutral → direction risk is low
  - Real risk: ONE leg gets liquidated while other stays open → full loss
  - Safe leverage for arb: 2–3× maximum
  - Each exchange requires margin = notional / leverage
  - Liquidation price buffer: keep 30% margin above maintenance
  - Max total exposure: 25% of account per exchange, 30% total
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ── Per-exchange taker fee rates ──────────────────────────────────────────────
EXCHANGE_TAKER_FEE: dict[str, float] = {
    "binance": 0.0004,    # 0.04%
    "bybit":   0.00055,   # 0.055%
    "gate":    0.00075,   # 0.075%
    "mexc":    0.0002,    # 0.02% (perpetual futures)
    "kucoin":  0.0006,    # 0.06% (futures taker)
}

# Base slippage in basis-points per side (rough estimate for mark-price gap)
EXCHANGE_BASE_SLIPPAGE_BPS: dict[str, float] = {
    "binance": 0.5,   # deep liquidity
    "bybit":   0.8,
    "gate":    2.0,
    "mexc":    1.5,
}

DEFAULT_FEE_RATE      = 0.00075   # fallback if exchange unknown
DEFAULT_SLIPPAGE_BPS  = 2.0

# Market-impact: extra bps per $100 k notional (linear)
MARKET_IMPACT_BPS_PER_100K = 2.0

# ── Leverage / margin constants ───────────────────────────────────────────────
# Safe max leverage for cross-exchange arb (delta-neutral but liquidation risk)
MAX_SAFE_LEVERAGE = 3.0

# Margin buffer above maintenance (30% safety cushion against liquidation)
MARGIN_BUFFER_PCT = 0.30

# Max % of account to put into a single pair (both legs combined)
MAX_PAIR_EXPOSURE_PCT = 0.03   # 3% of account per pair

# Max % of account in all open positions combined
MAX_TOTAL_EXPOSURE_PCT = 0.25  # 25% total

# Stop opening new positions if drawdown exceeds this
MAX_DRAWDOWN_STOP_PCT = 0.10   # 10% drawdown → pause new entries

# New listing score boost threshold (from symbol_watcher)
NEW_LISTING_SPREAD_MULT = 1.5  # allow 1.5× larger position for new listings


@dataclass
class SimResult:
    deal_size_usdt:       float
    entry_spread_pct:     float
    exit_spread_pct:      float
    slippage_entry_usdt:  float
    slippage_exit_usdt:   float
    fee_usdt:             float
    gross_pnl_usdt:       float
    net_pnl_usdt:         float
    net_pnl_pct:          float   # % of deal_size
    breakeven_spread_pct: float   # minimum spread to break even


@dataclass
class SimExecResult:
    """Book-walked (executable) round-trip result — Step C.

    P&L comes from the four VWAP prices actually crossed on the real books
    (bid-ask crossing on both entry AND exit + depth slippage are already
    baked in), so there is NO separate fixed-slippage term — only fees.
    """
    deal_size_usdt: float
    gross_pnl_usdt: float
    net_pnl_usdt:   float
    fee_usdt:       float
    net_pnl_pct:    float   # % of deal_size (one leg notional)
    pnl_long_usdt:  float
    pnl_short_usdt: float


class TradingSimulator:
    """
    Calculates realistic P&L for a single paper cross-exchange arb round-trip.

    Usage:
        sim = TradingSimulator(paper_deal_size_usdt=100.0)
        costs = sim.simulate_entry("bybit", "mexc", spread_pct=0.42)
        result = sim.simulate_trade("bybit", "mexc", entry_spread_pct=0.42, exit_spread_pct=0.05)
        size   = sim.position_size(account_usdt=1000.0, risk_pct=0.01, entry_spread_pct=0.42)
    """

    def __init__(self, paper_deal_size_usdt: float = 100.0) -> None:
        self.deal_size = paper_deal_size_usdt

    # ── Public API ─────────────────────────────────────────────────────────────

    def simulate_entry(
        self,
        exchange_long: str,
        exchange_short: str,
        spread_pct: float,   # noqa: ARG002  (reserved for future use)
        deal_size: Optional[float] = None,
    ) -> dict:
        """Returns entry-side cost components (USDT). Uses deal_size if provided."""
        size = deal_size if deal_size is not None else self.deal_size
        slip = self._slippage(exchange_long, size) + self._slippage(exchange_short, size)
        fees = self._fees(exchange_long, size) + self._fees(exchange_short, size)
        return {
            "slippage_usdt":    slip,
            "fee_usdt":         fees,
            "total_cost_usdt":  slip + fees,
        }

    def simulate_trade(
        self,
        exchange_long: str,
        exchange_short: str,
        entry_spread_pct: float,
        exit_spread_pct: float,
        deal_size: Optional[float] = None,
    ) -> SimResult:
        """
        Full 4-leg round-trip simulation.

        Gross PnL = deal_size * (entry_spread - exit_spread) / 100
          → When spread reverts from 0.5 % → 0 %, gross = deal_size × 0.5 %

        Fees  = (fee_long + fee_short) × 2  (entry + exit each leg)
        Slip  = (slip_long + slip_short) at entry + same at exit
        Net   = Gross − Fees − Slip_entry − Slip_exit

        deal_size overrides self.deal_size — used for dynamic MM sizing.
        """
        size = deal_size if deal_size is not None else self.deal_size
        slip_entry = self._slippage(exchange_long, size) + self._slippage(exchange_short, size)
        slip_exit  = self._slippage(exchange_long, size) + self._slippage(exchange_short, size)
        fees = (
            self._fees(exchange_long,  size) * 2   # long entry + long exit
            + self._fees(exchange_short, size) * 2   # short entry + short exit
        )
        gross = size * (entry_spread_pct - exit_spread_pct) / 100.0
        net   = gross - fees - slip_entry - slip_exit

        total_cost = fees + slip_entry + slip_exit
        breakeven  = total_cost / size * 100.0  # % spread needed to break even

        return SimResult(
            deal_size_usdt=size,
            entry_spread_pct=entry_spread_pct,
            exit_spread_pct=exit_spread_pct,
            slippage_entry_usdt=slip_entry,
            slippage_exit_usdt=slip_exit,
            fee_usdt=fees,
            gross_pnl_usdt=gross,
            net_pnl_usdt=net,
            net_pnl_pct=net / size * 100.0,
            breakeven_spread_pct=breakeven,
        )

    def simulate_exec_trade(
        self,
        exchange_long:   str,
        exchange_short:  str,
        deal_size:       float,
        entry_ask_long:  float,   # long leg BUYS at ask-VWAP  (entry)
        entry_bid_short: float,   # short leg SELLS at bid-VWAP (entry)
        exit_bid_long:   float,   # long leg SELLS at bid-VWAP  (exit)
        exit_ask_short:  float,   # short leg BUYS  at ask-VWAP (exit)
    ) -> SimExecResult:
        """
        Executable round-trip P&L from the four book-walked VWAP fill prices.

          pnl_long  = size * (exit_bid_long   − entry_ask_long)  / entry_ask_long
          pnl_short = size * (entry_bid_short − exit_ask_short)  / entry_bid_short
          gross     = pnl_long + pnl_short
          net       = gross − fees        (per-exchange taker fee × 2 legs × 2 sides)

        Bid-ask crossing (entry AND exit) and depth slippage are already inside
        the VWAP prices, so no fixed-slippage term is applied here.
        """
        size = deal_size
        pnl_long  = size * (exit_bid_long   - entry_ask_long)  / entry_ask_long
        pnl_short = size * (entry_bid_short - exit_ask_short)  / entry_bid_short
        gross = pnl_long + pnl_short
        fees = (
            self._fees(exchange_long,  size) * 2   # long entry + long exit
            + self._fees(exchange_short, size) * 2   # short entry + short exit
        )
        net = gross - fees
        return SimExecResult(
            deal_size_usdt=size,
            gross_pnl_usdt=gross,
            net_pnl_usdt=net,
            fee_usdt=fees,
            net_pnl_pct=(net / size * 100.0) if size else 0.0,
            pnl_long_usdt=pnl_long,
            pnl_short_usdt=pnl_short,
        )

    def position_size(
        self,
        account_usdt: float,
        risk_pct: float,
        entry_spread_pct: float,
        max_usdt: float = 500.0,
        is_new_listing: bool = False,
    ) -> float:
        """
        Fixed-fractional position sizing scaled by spread edge.

        Args:
            account_usdt:     Virtual paper account balance.
            risk_pct:         Fraction of account to risk per trade (e.g. 0.01 = 1 %).
            entry_spread_pct: Entry spread — larger edge → bigger size.
            max_usdt:         Hard cap on position size.
            is_new_listing:   New listings get a 1.5× boost (larger spreads expected).

        Returns USDT notional for one leg (long = short = this amount).
        """
        base = account_usdt * risk_pct
        # Scale: 1× at 0.30 %, 2× at 0.60 %, capped at 3×
        spread_mult = min(entry_spread_pct / 0.30, 3.0)
        if is_new_listing:
            spread_mult = min(spread_mult * NEW_LISTING_SPREAD_MULT, 4.0)
        return min(base * spread_mult, max_usdt)

    def leverage_position_size(
        self,
        account_usdt: float,
        open_positions_count: int,
        entry_spread_pct: float,
        leverage: float = 2.0,
        is_new_listing: bool = False,
        current_drawdown_pct: float = 0.0,
    ) -> dict:
        """
        Leverage-aware position sizing with full risk checks.

        Returns dict with:
          - notional_usdt:   trade size (what gets sent to exchange)
          - margin_usdt:     collateral needed per leg (notional / leverage)
          - leverage:        actual leverage used (capped at MAX_SAFE_LEVERAGE)
          - allowed:         False if any risk gate blocks the trade
          - reason:          why blocked (if not allowed)
          - liquidation_gap: % move on one leg that triggers liquidation

        Cross-exchange arb risk model:
          - Each leg requires margin = notional / leverage at its exchange
          - Liquidation happens if price moves against ONE leg by (1/leverage - buffer)
          - Since legs are on DIFFERENT exchanges, they can diverge temporarily
          - Buffer of 30% above maintenance keeps us safe from temporary divergence
        """
        leverage = min(max(leverage, 1.0), MAX_SAFE_LEVERAGE)

        # ── Risk gates ────────────────────────────────────────────────────────
        if current_drawdown_pct >= MAX_DRAWDOWN_STOP_PCT:
            return {
                "notional_usdt": 0, "margin_usdt": 0, "leverage": leverage,
                "allowed": False,
                "reason": f"Drawdown {current_drawdown_pct*100:.1f}% ≥ stop {MAX_DRAWDOWN_STOP_PCT*100:.0f}%",
            }

        max_per_pair = account_usdt * MAX_PAIR_EXPOSURE_PCT
        max_total    = account_usdt * MAX_TOTAL_EXPOSURE_PCT
        already_used = open_positions_count * self.deal_size  # approximate
        if already_used >= max_total:
            return {
                "notional_usdt": 0, "margin_usdt": 0, "leverage": leverage,
                "allowed": False,
                "reason": f"Total exposure {already_used:.0f} USDT ≥ {max_total:.0f} limit",
            }

        # ── Size calculation ──────────────────────────────────────────────────
        base_size    = account_usdt * MAX_PAIR_EXPOSURE_PCT
        spread_mult  = min(entry_spread_pct / 0.30, 3.0)
        if is_new_listing:
            spread_mult = min(spread_mult * NEW_LISTING_SPREAD_MULT, 4.0)

        notional = min(base_size * spread_mult, max_per_pair)
        margin_per_leg = notional / leverage

        # How far price can move before liquidation (per leg, with buffer)
        # maintenance_margin typically 0.5% on most exchanges
        maintenance_pct = 0.005
        liquidation_gap_pct = (1 / leverage - maintenance_pct) * (1 - MARGIN_BUFFER_PCT) * 100

        return {
            "notional_usdt":    round(notional, 2),
            "margin_usdt":      round(margin_per_leg, 2),
            "margin_total":     round(margin_per_leg * 2, 2),  # both legs
            "leverage":         leverage,
            "allowed":          True,
            "reason":           None,
            "liquidation_gap_pct": round(liquidation_gap_pct, 2),
            "is_new_listing":   is_new_listing,
        }

    @staticmethod
    def compute_sharpe(net_pnl_pcts: list[float], avg_hold_minutes: float = 15.0) -> Optional[float]:
        """
        Annualised Sharpe ratio from per-trade net PnL percentages.

        Args:
            net_pnl_pcts:     List of per-trade net PnL as % of deal_size.
            avg_hold_minutes: Average hold time (used to annualise).

        Returns None if insufficient data.
        """
        n = len(net_pnl_pcts)
        if n < 5:
            return None
        mean = sum(net_pnl_pcts) / n
        variance = sum((x - mean) ** 2 for x in net_pnl_pcts) / n
        std = variance ** 0.5
        if std < 1e-9:
            return None
        trades_per_year = 365 * 24 * 60 / avg_hold_minutes
        return mean / std * math.sqrt(trades_per_year)

    @staticmethod
    def compute_max_drawdown(cumulative_pnl: list[float]) -> float:
        """
        Maximum drawdown as a fraction of peak equity (0.0 – 1.0).

        Example: 0.05 = 5 % drawdown from peak.
        """
        if not cumulative_pnl:
            return 0.0
        peak   = cumulative_pnl[0]
        max_dd = 0.0
        for v in cumulative_pnl:
            if v > peak:
                peak = v
            if peak > 0:
                dd = (peak - v) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    # ── Private helpers ────────────────────────────────────────────────────────

    def _slippage(self, exchange: str, size: Optional[float] = None) -> float:
        """One-side slippage USDT = (base_bps + impact_bps) / 10000 * size."""
        s = size if size is not None else self.deal_size
        base_bps   = EXCHANGE_BASE_SLIPPAGE_BPS.get(exchange.lower(), DEFAULT_SLIPPAGE_BPS)
        impact_bps = (s / 100_000.0) * MARKET_IMPACT_BPS_PER_100K
        return s * (base_bps + impact_bps) / 10_000.0

    def _fees(self, exchange: str, size: Optional[float] = None) -> float:
        """One-side taker fee USDT."""
        s = size if size is not None else self.deal_size
        rate = EXCHANGE_TAKER_FEE.get(exchange.lower(), DEFAULT_FEE_RATE)
        return s * rate
