"""
TradingSimulator — realistic PnL modelling for paper cross-exchange arb.

Accounts for:
  - Per-exchange taker fees
  - Base slippage (per exchange) + market-impact component
  - 4-leg round-trip cost (long entry, short entry, long exit, short exit)
  - Position sizing (fixed-fractional / Kelly-inspired)
  - Sharpe ratio (annualised) and max drawdown utilities
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ── Per-exchange taker fee rates ──────────────────────────────────────────────
EXCHANGE_TAKER_FEE: dict[str, float] = {
    "binance": 0.0004,    # 0.04%
    "bybit":   0.00055,   # 0.055%
    "gate":    0.00075,   # 0.075%
    "mexc":    0.0002,    # 0.02% (perpetual futures)
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
        spread_pct: float,  # noqa: ARG002  (reserved for future use)
    ) -> dict:
        """Returns entry-side cost components (USDT)."""
        slip = self._slippage(exchange_long) + self._slippage(exchange_short)
        fees = self._fees(exchange_long) + self._fees(exchange_short)
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
    ) -> SimResult:
        """
        Full 4-leg round-trip simulation.

        Gross PnL = deal_size * (entry_spread - exit_spread) / 100
          → When spread reverts from 0.5 % → 0 %, gross = deal_size × 0.5 %

        Fees  = (fee_long + fee_short) × 2  (entry + exit each leg)
        Slip  = (slip_long + slip_short) at entry + same at exit
        Net   = Gross − Fees − Slip_entry − Slip_exit
        """
        slip_entry = self._slippage(exchange_long) + self._slippage(exchange_short)
        slip_exit  = self._slippage(exchange_long) + self._slippage(exchange_short)
        fees = (
            self._fees(exchange_long)  * 2   # long entry + long exit
            + self._fees(exchange_short) * 2   # short entry + short exit
        )
        gross = self.deal_size * (entry_spread_pct - exit_spread_pct) / 100.0
        net   = gross - fees - slip_entry - slip_exit

        total_cost = fees + slip_entry + slip_exit
        breakeven  = total_cost / self.deal_size * 100.0  # % spread needed to break even

        return SimResult(
            deal_size_usdt=self.deal_size,
            entry_spread_pct=entry_spread_pct,
            exit_spread_pct=exit_spread_pct,
            slippage_entry_usdt=slip_entry,
            slippage_exit_usdt=slip_exit,
            fee_usdt=fees,
            gross_pnl_usdt=gross,
            net_pnl_usdt=net,
            net_pnl_pct=net / self.deal_size * 100.0,
            breakeven_spread_pct=breakeven,
        )

    def position_size(
        self,
        account_usdt: float,
        risk_pct: float,
        entry_spread_pct: float,
        max_usdt: float = 500.0,
    ) -> float:
        """
        Fixed-fractional position sizing scaled by spread edge.

        Args:
            account_usdt:     Virtual paper account balance.
            risk_pct:         Fraction of account to risk per trade (e.g. 0.01 = 1 %).
            entry_spread_pct: Entry spread — larger edge → bigger size.
            max_usdt:         Hard cap on position size.

        Returns USDT notional for one leg (long = short = this amount).
        """
        base = account_usdt * risk_pct
        # Scale: 1× at 0.30 %, 2× at 0.60 %, capped at 3×
        spread_mult = min(entry_spread_pct / 0.30, 3.0)
        return min(base * spread_mult, max_usdt)

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

    def _slippage(self, exchange: str) -> float:
        """One-side slippage USDT = (base_bps + impact_bps) / 10000 * deal_size."""
        base_bps   = EXCHANGE_BASE_SLIPPAGE_BPS.get(exchange.lower(), DEFAULT_SLIPPAGE_BPS)
        impact_bps = (self.deal_size / 100_000.0) * MARKET_IMPACT_BPS_PER_100K
        return self.deal_size * (base_bps + impact_bps) / 10_000.0

    def _fees(self, exchange: str) -> float:
        """One-side taker fee USDT."""
        rate = EXCHANGE_TAKER_FEE.get(exchange.lower(), DEFAULT_FEE_RATE)
        return self.deal_size * rate
