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


def _sources() -> dict:
    """Per-venue spot-mark source. 'live-book' | 'snapshot'."""
    out = {"mexc": "live-book", "gate": "snapshot"}
    raw = os.getenv("CARRY_SPOT_MARK_SOURCES", "")
    for part in (p for p in raw.split(",") if ":" in p):
        ex, src = part.split(":", 1)
        out[ex.strip()] = src.strip()
    return out


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
    # The BINDING basis test is on the CURRENT basis (median over the trailing
    # `basis_now_window_h`), not on the lookback mean. A mean averages away
    # exactly the condition this gate exists to detect.
    max_basis_bps: float = _f("CARRY_MAX_BASIS_BPS", 150.0)
    basis_now_window_h: float = _f("CARRY_BASIS_NOW_WINDOW_H", 2.0)
    # The lookback mean is retained as a SECONDARY, strictly looser test — it
    # catches the opposite failure (chronically wild basis, one calm hour) and
    # can only ever reject in addition to the current-basis test, never admit.
    basis_mean_mult: float = _f("CARRY_BASIS_MEAN_MULT", 2.0)
    # Trailing window for the BOOKED basis marks at entry and at exit. One
    # point read is not a measurement here: intraday basis SD is 9-59 bps
    # against moves of interest of 20-40 bps. Trailing rather than centred
    # because at entry a live bot has no forward half, and the entry and exit
    # marks must be the SAME estimator.
    basis_mark_window_h: float = _f("CARRY_BASIS_MARK_WINDOW_H", 2.0)
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
    # R4's floor is ANCHORED at this settlement interval and carried to every
    # other interval as a PER-EPOCH rate. See `hold_apr_floor`. 4 h is the
    # anchor because it is the majority of the live universe (800 of 1,204
    # resolved names on 2026-09-04), so the 4 h floor — and the set of names it
    # admits — is unchanged by this fix. Only the 8 h names move.
    funding_floor_ref_interval_h: float = _f("CARRY_FUNDING_FLOOR_REF_IV_H", 4.0)
    depth_collapse_ratio: float = _f("CARRY_DEPTH_COLLAPSE", 0.50)     # R5
    rebalance_delta_pct: float = _f("CARRY_REBALANCE_DELTA_PCT", 1.0)  # (c)
    # ---- neutrality hysteresis (2026-08-23, Phase 3b/3) -------------------
    # mexc/BTW rebalanced 5x in 43 min on a SAWTOOTH delta
    # (0.00 -> +0.99 -> -1.01 -> +1.27) that was a measurement artifact, not
    # drift. Same "noise drives action" class as the TUT churn, ~1000x cheaper,
    # but it pollutes the paper record. Two independent brakes:
    #   1. a breach must PERSIST for N consecutive DISTINCT marks before we
    #      trade. Distinct, not merely consecutive cycles: the loop runs every
    #      ~65s but mexc spot depth lands every ~8 min, so three cycles could
    #      be three re-reads of ONE book, which confirms nothing. Only a new
    #      mark timestamp advances the counter.
    #   2. we correct to the deadband EDGE, not to zero, so each correction
    #      trades less
    rebalance_confirm_cycles: int = _i("CARRY_REBALANCE_CONFIRM_CYCLES", 3)
    rebalance_deadband_pct: float = _f("CARRY_REBALANCE_DEADBAND_PCT", 0.3)
    # Two books count as ONE observation only if they were taken within this
    # many seconds of each other. Perp depth lands every ~2 min, so the perp
    # book nearest a spot book is normally <1 min away; beyond 90s the pair is
    # not a simultaneous mark and neutrality is left UNEVALUATED.
    mark_pair_max_skew_sec: float = _f("CARRY_MARK_PAIR_SKEW_SEC", 90.0)
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

    # ---- spot mark source, PINNED PER VENUE (2026-08-23) ------------------
    # NEVER switch source mid-position. The age-based fallback added in 3b/2
    # flipped mexc between the REST snapshot and the live book as spot_age
    # crossed 15 min; the two disagree ~0.8% on average and 2.1% on BTW, so the
    # flip alone manufactured a >1% "drift" that tripped the rebalance rule.
    #   mexc  -> live-book: its spot REST is Akamai-403 flaky (29 min stale)
    #   gate  -> snapshot:  fresh (~4 min) and reliable
    # Override with CARRY_SPOT_MARK_SOURCES="mexc:live-book,gate:snapshot".
    spot_mark_sources: dict = field(default_factory=lambda: _sources())

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
    def min_hold_rate_per_epoch(self) -> float:
        """R4's exit floor as a PER-EPOCH funding rate.

        This is the unit the data actually lives in, and it is deliberately
        INTERVAL-INDEPENDENT: R4 asks "has this name stopped paying?", which is
        a per-settlement condition. Whether the resulting annual yield is
        attractive is a different question, and it belongs to the selector
        (`min_net_apr`) at entry, not to the exit rule.
        """
        eps_per_year = 365.0 * 24.0 / self.funding_floor_ref_interval_h
        return (self.min_hold_apr / 100.0) * self.capital_multiple / eps_per_year

    def hold_apr_floor(self, iv_hours: float) -> float:
        """R4's exit floor for a name settling every `iv_hours`, as APR ON
        CAPITAL so it is directly comparable with `apr7_cap`.

        THE BUG THIS FIXES. The floor used to be one flat annual number applied
        to an interval-annualised rate, so the SAME per-epoch rate landed on
        opposite sides of it depending only on the settlement interval. The
        universal venue default of 5e-05 — a rest value carrying no information
        about the name, and 84% sticky once pinned — sits at 91% of the floor
        for a 4 h name but only 46% of it for an 8 h name. So an 8 h name that
        merely reverted to the default was exited BY CONSTRUCTION, with nothing
        in the exit about the name. It forced 5 of 14 round trips in the first
        window, every one of them Gate, and quietly ratcheted the book toward
        MEXC's 4 h names without R7 ever being consulted.

        This is the same root cause as the T54 interval bug that understated
        795 of 1,197 symbols by 2x. That one was fixed in the SELECTOR and not
        in the exit rule; both now read the same interval-aware floor, which
        also keeps the TUT hysteresis intact (entry and exit must never test
        different metrics).

            4 h names ->  8.0% on capital (12.0% gross)   [unchanged, the anchor]
            8 h names ->  4.0% on capital ( 6.0% gross)   [was 8.0% / 12.0%]
        """
        if not iv_hours or iv_hours <= 0:
            return self.min_hold_apr
        eps_per_year = 365.0 * 24.0 / iv_hours
        return (self.min_hold_rate_per_epoch * eps_per_year * 100.0
                / self.capital_multiple)

    def reentry_apr_floor(self, iv_hours: float) -> float:
        """The re-entry bar, STRICTLY ABOVE the R4 exit floor at the SAME
        interval. The gap is the hysteresis band: inside it a name is neither
        held nor re-opened, which is what stops the flip-flop."""
        return self.hold_apr_floor(iv_hours) * self.reentry_apr_mult

    @property
    def reentry_apr(self) -> float:
        """Re-entry bar at the ANCHOR interval. Prefer `reentry_apr_floor(iv)`;
        this remains only for logging where no interval is in hand."""
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
                f"@{self.funding_floor_ref_interval_h:.0f}h anchor "
                f"(8h:{self.hold_apr_floor(8.0):.0f}%) "
                f"reenter>{self.reentry_apr:.0f}% cooldown={self.reentry_cooldown_hours:.0f}h "
                f"payback<={self.max_payback_days:.1f}d H{self.hold_days} "
                f"| neutrality {self.rebalance_delta_pct:.2f}% x"
                f"{self.rebalance_confirm_cycles} cycles -> band "
                f"{self.rebalance_deadband_pct:.2f}% (distinct marks, skew<="
                f"{self.mark_pair_max_skew_sec:.0f}s) "
                f"| spot marks {self.spot_mark_sources}")
