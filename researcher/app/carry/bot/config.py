"""Carry bot configuration — one flag separates paper from live.

Every risk parameter in CARRY_BOT_DESIGN.md §2 lives here and nowhere else, so
the design document and the running code cannot drift apart silently.

THREE INDEPENDENT LOCKS keep this in paper mode:
    1. CARRY_BOT_MODE must be "live"
    2. CARRY_BOT_ALLOW_LIVE must be "1"
    3. LiveExecutor must actually be implemented (it raises today)
No single mistake can arm real trading.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class CarryBotConfig:
    # ---- mode -------------------------------------------------------------
    mode: str = os.getenv("CARRY_BOT_MODE", "paper").lower()
    allow_live: bool = os.getenv("CARRY_BOT_ALLOW_LIVE", "0") == "1"

    # ---- capital ----------------------------------------------------------
    capital_eur: float = _f("CARRY_CAPITAL_EUR", 1000.0)
    eurusd: float = _f("CARRY_EURUSD", 1.08)
    leverage: float = _f("CARRY_LEVERAGE", 2.0)          # R1, hard-capped below

    # ---- selection gates --------------------------------------------------
    lookback_days: int = _i("CARRY_LOOKBACK_DAYS", 14)
    min_epochs: int = _i("CARRY_MIN_EPOCHS", 12)
    min_positive_frac: float = _f("CARRY_MIN_POS_FRAC", 0.80)
    max_rt_spread_bps: float = _f("CARRY_MAX_RT_SPREAD_BPS", 60.0)
    max_basis_bps: float = _f("CARRY_MAX_BASIS_BPS", 150.0)
    min_net_apr: float = _f("CARRY_MIN_NET_APR", 8.0)     # net-on-capital, H30
    hold_days: int = _i("CARRY_HOLD_DAYS", 30)            # amortisation horizon

    # ---- sizing / caps ----------------------------------------------------
    max_rt_slip_bps: float = _f("CARRY_MAX_RT_SLIP_BPS", 50.0)   # R6
    mexc_venue_cap: float = _f("CARRY_MEXC_VENUE_CAP", 0.40)     # R7
    max_positions: int = _i("CARRY_MAX_POSITIONS", 12)           # R10
    min_notional_usd: float = _f("CARRY_MIN_NOTIONAL_USD", 25.0)

    # ---- risk rules -------------------------------------------------------
    liquidation_buffer_pct: float = _f("CARRY_LIQ_BUFFER_PCT", 35.0)   # R2
    margin_topup_move_pct: float = _f("CARRY_MARGIN_TOPUP_PCT", 20.0)  # R3
    flip_exit_epochs: int = _i("CARRY_FLIP_EXIT_EPOCHS", 2)            # R4
    min_hold_apr: float = _f("CARRY_MIN_HOLD_APR", 8.0)                # R4
    depth_collapse_ratio: float = _f("CARRY_DEPTH_COLLAPSE", 0.50)     # R5
    rebalance_delta_pct: float = _f("CARRY_REBALANCE_DELTA_PCT", 1.0)  # (c)
    max_drawdown_pct: float = _f("CARRY_MAX_DRAWDOWN_PCT", 5.0)        # R9
    max_data_staleness_min: float = _f("CARRY_MAX_STALENESS_MIN", 15.0)  # R9/f
    kill_switch_file: str = os.getenv(
        "CARRY_KILL_SWITCH", "/home/vadym/mexc-trade-bot/CARRY_BOT_KILL")  # R8

    # ---- fees (bps per leg) ----------------------------------------------
    maker_bps: dict = field(default_factory=lambda: {"mexc": 1.0, "gate": 2.0})
    taker_bps: dict = field(default_factory=lambda: {"mexc": 5.0, "gate": 5.0})

    # ---- depth / worst hour ----------------------------------------------
    depth_lookback_hours: int = _i("CARRY_DEPTH_LOOKBACK_H", 168)
    min_hod_buckets: int = _i("CARRY_MIN_HOD_BUCKETS", 6)
    min_snaps_per_hod: int = _i("CARRY_MIN_SNAPS_PER_HOD", 3)

    # ---- loop -------------------------------------------------------------
    tick_secs: float = _f("CARRY_TICK_SECS", 60.0)
    select_every_min: float = _f("CARRY_SELECT_EVERY_MIN", 60.0)

    def __post_init__(self) -> None:
        # R1: leverage is hard-capped in code, not merely by convention.
        if self.leverage > 2.0 or self.leverage < 1.0:
            object.__setattr__(self, "leverage", min(2.0, max(1.0, self.leverage)))

    @property
    def is_live(self) -> bool:
        """Live requires BOTH locks. Anything else is paper."""
        return self.mode == "live" and self.allow_live

    @property
    def capital_usd(self) -> float:
        return self.capital_eur * self.eurusd

    @property
    def capital_multiple(self) -> float:
        """Capital consumed per unit of spot notional: C = S + S/L."""
        return 1.0 + 1.0 / self.leverage

    def describe(self) -> str:
        return (f"mode={self.mode} (live_armed={self.is_live}) "
                f"capital=EUR{self.capital_eur:,.0f} L={self.leverage:.1f}x "
                f"mexc_cap={self.mexc_venue_cap:.0%} slip_cap={self.max_rt_slip_bps:.0f}bps "
                f"min_apr={self.min_net_apr:.1f}%")
