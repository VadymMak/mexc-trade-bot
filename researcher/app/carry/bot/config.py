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
    min_net_apr: float = _f("CARRY_MIN_NET_APR", 8.0)     # net-on-capital
    # Amortisation horizon. WAS 30 — a horizon we had never actually achieved,
    # which flattered every candidate's net APR. The observed median hold over
    # the first paper run was 44 MINUTES. 14d is still optimistic but it is at
    # least inside the range the surviving positions have demonstrated.
    hold_days: int = _i("CARRY_HOLD_DAYS", 14)

    # ---- re-entry hysteresis (2026-08-23) — fixes the TUT churn loop -------
    # R4 exits on the TRAILING-7 APR; the selector used to re-admit on the
    # 14-DAY MEAN. Two different metrics, so a name could fail the exit test
    # and pass the entry test in the same hour: TUT round-tripped 23 times in
    # 48h, burning $8.26 of cost to collect $0.11 of funding. The gates now
    # read the SAME trailing metric, separated by a hysteresis band.
    reentry_cooldown_hours: float = _f("CARRY_REENTRY_COOLDOWN_H", 48.0)
    reentry_apr_mult: float = _f("CARRY_REENTRY_APR_MULT", 2.0)
    reentry_memory_days: float = _f("CARRY_REENTRY_MEMORY_DAYS", 7.0)
    max_cooldown_stacks: int = _i("CARRY_MAX_COOLDOWN_STACKS", 3)

    # ---- cost gate --------------------------------------------------------
    # A position must repay its own round trip fast enough to be worth opening.
    cost_gate_mult: float = _f("CARRY_COST_GATE_MULT", 3.0)
    min_hold_days: float = _f("CARRY_MIN_HOLD_DAYS", 14.0)

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
    # Beyond limit x this, a venue's data is dead and holding an unmonitorable
    # leveraged position there is the larger risk -> derisk that venue only.
    stale_derisk_multiple: float = _f("CARRY_STALE_DERISK_MULT", 4.0)

    # ---- remediation (2026-08-23) ----------------------------------------
    # Extra buffer a top-up/derisk aims for beyond the line it just crossed, so
    # remediation does not re-fire on the next tick at the same threshold.
    remediation_headroom_pp: float = _f("CARRY_REMEDIATION_HEADROOM_PP", 5.0)
    min_effective_leverage: float = _f("CARRY_MIN_EFF_LEVERAGE", 1.0)
    max_derisk_fraction: float = _f("CARRY_MAX_DERISK_FRACTION", 0.50)
    max_rebalance_fraction: float = _f("CARRY_MAX_REBALANCE_FRACTION", 0.25)
    kill_switch_file: str = os.getenv(
        "CARRY_KILL_SWITCH", "/home/vadym/mexc-trade-bot/CARRY_BOT_KILL")  # R8

    # ---- fees (bps per leg) ----------------------------------------------
    maker_bps: dict = field(default_factory=lambda: {"mexc": 1.0, "gate": 2.0})
    taker_bps: dict = field(default_factory=lambda: {"mexc": 5.0, "gate": 5.0})

    # ---- depth / worst hour ----------------------------------------------
    depth_lookback_hours: int = _i("CARRY_DEPTH_LOOKBACK_H", 168)
    min_hod_buckets: int = _i("CARRY_MIN_HOD_BUCKETS", 6)
    min_snaps_per_hod: int = _i("CARRY_MIN_SNAPS_PER_HOD", 3)
    # Snapshots sampled per (day, hour) bucket. 4 x 24h x 7d = 672 per leg,
    # against ~6,100 unsampled. The per-bucket median is unchanged; the memory
    # is not. Per (day,hour) rather than per hour-of-day so all 7 days are
    # represented — otherwise the sample collapses onto the last 24h.
    max_snaps_per_hour: int = _i("CARRY_MAX_SNAPS_PER_HOUR", 4)
    # Hard cap on cached per-leg curve sets. The cache is a within-name
    # optimisation for the 24-step binary search, not a pass-wide store.
    book_cache_entries: int = _i("CARRY_BOOK_CACHE_ENTRIES", 16)

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
    def reentry_apr(self) -> float:
        """Re-entry bar on the trailing-7 APR, STRICTLY ABOVE the R4 exit floor.

        The gap between min_hold_apr and this is the hysteresis band: inside it
        a name is neither held nor re-opened, which is exactly the state that
        stops the flip-flop.
        """
        return self.min_hold_apr * self.reentry_apr_mult

    @property
    def max_payback_days(self) -> float:
        """Round-trip cost must be repaid within this many days of funding, or
        the position is not worth opening at all."""
        return self.min_hold_days / self.cost_gate_mult

    @property
    def capital_multiple(self) -> float:
        """Capital consumed per unit of spot notional: C = S + S/L."""
        return 1.0 + 1.0 / self.leverage

    def describe(self) -> str:
        return (f"mode={self.mode} (live_armed={self.is_live}) "
                f"capital=EUR{self.capital_eur:,.0f} L={self.leverage:.1f}x "
                f"mexc_cap={self.mexc_venue_cap:.0%} slip_cap={self.max_rt_slip_bps:.0f}bps "
                f"min_apr={self.min_net_apr:.1f}% | exit<{self.min_hold_apr:.0f}% "
                f"reenter>{self.reentry_apr:.0f}% cooldown={self.reentry_cooldown_hours:.0f}h "
                f"payback<={self.max_payback_days:.1f}d H{self.hold_days}")
