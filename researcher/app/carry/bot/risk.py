"""Risk manager and neutrality manager — rules R1..R10 of CARRY_BOT_DESIGN.md §2.

Every rule returns a Verdict whether or not it fires, and the caller logs all of
them. A rule that never fires must be visible as TESTED AND QUIET rather than as
possibly-not-wired-up — that distinction is the whole lesson of the zombie
socket, where absence of output was mistaken for health.
"""
from __future__ import annotations

import logging
import os

from .book import worst_hour_capacity

logger = logging.getLogger(__name__)


class Verdict:
    __slots__ = ("rule", "fired", "action", "detail", "data")

    def __init__(self, rule: str, fired: bool, action: str = "none",
                 detail: str = "", data: dict | None = None) -> None:
        self.rule, self.fired, self.action = rule, fired, action
        self.detail, self.data = detail, data or {}

    def __repr__(self):
        return f"<{self.rule} {'FIRED' if self.fired else 'ok'} {self.action}: {self.detail}>"


class NeutralityManager:
    """(c) — tracks delta drift between the two legs and calls rebalances.

    The legs drift apart as the basis moves even with quantities unchanged:
    spot marks at spot, perp marks at the perp mark, and the difference is live
    directional exposure we did not intend to have.
    """

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def check(self, notional_usd: float, spot_entry: float, perp_entry: float,
              spot_now: float, perp_now: float,
              perp_notional: float | None = None) -> Verdict:
        """`perp_notional` defaults to the spot notional (the state at entry).
        After a rebalance the two legs differ on purpose, and measuring both
        against one number would hide the correction we just made."""
        if not (spot_entry and perp_entry and spot_now and perp_now):
            return Verdict("neutrality", False, "none", "prices unavailable")
        pn = notional_usd if perp_notional is None else perp_notional
        spot_qty = notional_usd / spot_entry
        perp_qty = pn / perp_entry
        delta = spot_qty * spot_now - perp_qty * perp_now
        pct = 100.0 * delta / notional_usd if notional_usd else 0.0
        fired = abs(pct) > self._cfg.rebalance_delta_pct
        return Verdict(
            "neutrality", fired, "rebalance" if fired else "none",
            f"delta {delta:+,.2f} USD ({pct:+.2f}% of notional, "
            f"threshold {self._cfg.rebalance_delta_pct:.2f}%)",
            {"delta_usd": delta, "delta_pct": pct})


class RiskManager:
    def __init__(self, cfg, books, store) -> None:
        self._cfg = cfg
        self._books = books
        self._store = store

    # ---- R8/R9 global -----------------------------------------------------
    def kill_switch(self) -> Verdict:
        path = self._cfg.kill_switch_file
        present = os.path.exists(path)
        return Verdict("R8-kill-switch", present,
                       "halt" if present else "none",
                       f"{path} {'PRESENT — halting' if present else 'absent'}")

    def data_staleness(self, exchange: str, funding_age_min: float,
                       book_age_min: float) -> Verdict:
        """Freshness of ONE venue's data.

        Table-wide max(ts) was the bug: Gate writing every 5 min kept the global
        clock fresh while MEXC sat 73 minutes dark, and the bot held a MEXC
        position the whole time. A position on venue X must be governed by
        venue X's own data.

        Two levels: stale => that venue is frozen (no opens, and rules that read
        funding data are not evaluated on data we know is old). Very stale =>
        holding an unmonitorable leveraged position is itself the larger risk,
        so that venue's positions are derisked.
        """
        worst = max(funding_age_min, book_age_min)
        limit = self._cfg.max_data_staleness_min
        hard = limit * self._cfg.stale_derisk_multiple
        fired = worst > limit
        action = "derisk" if worst > hard else ("freeze" if fired else "none")
        return Verdict(
            "R9-data-staleness", fired, action,
            f"{exchange}: funding {funding_age_min:.1f}min / book "
            f"{book_age_min:.1f}min (limit {limit:.0f}min, derisk above "
            f"{hard:.0f}min)"
            + (" — VENUE FROZEN, no opens and no decisions on stale data"
               if action == "freeze" else "")
            + (" — VENUE DATA DEAD, derisking its positions"
               if action == "derisk" else ""),
            {"exchange": exchange, "funding_age_min": funding_age_min,
             "book_age_min": book_age_min})

    def drawdown(self, pnl_usd: float, deployed_usd: float) -> Verdict:
        if deployed_usd <= 0:
            return Verdict("R9-drawdown", False, "none", "nothing deployed")
        pct = 100.0 * pnl_usd / deployed_usd
        fired = pct < -self._cfg.max_drawdown_pct
        return Verdict("R9-drawdown", fired, "halt" if fired else "none",
                       f"P&L {pct:+.2f}% of deployed "
                       f"(limit -{self._cfg.max_drawdown_pct:.1f}%)",
                       {"pnl_pct": pct})

    # ---- R4 funding flip --------------------------------------------------
    async def funding_flip(self, ex: str, sym: str, iv_hours: float) -> Verdict:
        """Exit on consecutive negative epochs, or when the trailing APR falls
        below the level at which the position stops paying for its own exit."""
        rows = await self._store._pool.fetch(
            """WITH ep AS (
                 SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$3))::bigint)
                        (floor(extract(epoch FROM ts)/$3))::bigint AS e, funding_rate
                 FROM funding_basis_snapshots
                 WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
                       AND ts > now() - interval '3 days'
                 ORDER BY e, ts DESC)
               SELECT funding_rate FROM ep ORDER BY e DESC LIMIT 7""",
            ex, sym, int(iv_hours * 3600))
        rates = [float(r["funding_rate"]) for r in rows]
        if not rates:
            return Verdict("R4-funding-flip", False, "none", "no recent epochs")
        neg_run = 0
        for r in rates:
            if r < 0:
                neg_run += 1
            else:
                break
        apr7 = (sum(rates) / len(rates)) * (24.0 / iv_hours) * 365.0 * 100.0
        apr7_cap = apr7 / self._cfg.capital_multiple
        flip = neg_run >= self._cfg.flip_exit_epochs
        weak = apr7_cap < self._cfg.min_hold_apr
        fired = flip or weak
        why = ("negative %d epochs in a row" % neg_run if flip
               else "trailing-7 APR %.1f%% on capital < %.1f%%" % (apr7_cap, self._cfg.min_hold_apr)
               if weak else "healthy")
        return Verdict("R4-funding-flip", fired, "exit" if fired else "none",
                       f"{why} (trailing-7 {apr7_cap:.1f}% on capital, "
                       f"{neg_run} negative epochs)",
                       {"apr7_on_capital": apr7_cap, "neg_run": neg_run,
                        "rates": rates})

    # ---- R5 depth collapse ------------------------------------------------
    async def depth_collapse(self, ex: str, sym: str, notional_usd: float,
                             entry_depth_usd: float) -> Verdict:
        by_hod = await self._books.leg_curves(ex, sym, "spot", "bid")
        now_depth, hour, basis = worst_hour_capacity(
            by_hod, self._cfg.max_rt_slip_bps, self._cfg)
        ratio = (now_depth / entry_depth_usd) if entry_depth_usd else 1.0
        collapsed = ratio < self._cfg.depth_collapse_ratio
        too_big = now_depth < notional_usd
        fired = collapsed or too_big
        why = ("worst-hour exit depth ${:,.0f} < position ${:,.0f}".format(
                   now_depth, notional_usd) if too_big
               else "worst-hour depth {:.0%} of entry".format(ratio) if collapsed
               else "depth healthy")
        return Verdict("R5-depth-collapse", fired, "exit" if fired else "none",
                       f"{why} (now ${now_depth:,.0f} vs entry ${entry_depth_usd:,.0f}, "
                       f"basis={basis}"
                       + (f", thinnest {hour:02d}:00 UTC" if hour is not None else "") + ")",
                       {"now_depth_usd": now_depth, "entry_depth_usd": entry_depth_usd,
                        "ratio": ratio, "basis": basis})

    # ---- R2/R3 margin -----------------------------------------------------
    def margin(self, perp_entry: float, perp_now: float,
               leverage: float | None = None) -> Verdict:
        """The short perp loses as price rises. The spot leg gains, but it sits
        on the OTHER venue's balance, so it does not defend perp margin.

        Leverage is the POSITION's current effective leverage, not the config
        default — a margin top-up or a derisk lowers it, and reading the config
        instead would make the rule permanently blind to its own remediation.
        """
        if not (perp_entry and perp_now):
            return Verdict("R2-margin", False, "none", "no mark")
        lev = float(leverage or self._cfg.leverage)
        move_pct = 100.0 * (perp_now - perp_entry) / perp_entry
        # at leverage L the short is wiped by roughly a +100/L % move
        liq_move = 100.0 / lev
        buffer_left = liq_move - move_pct
        # Thresholds are on the BUFFER REMAINING, so they keep their meaning
        # after leverage changes. At the original 2x these are the same numbers
        # as before: 50 - 20 = 30pp topup line, 50 - 35 = 15pp derisk line.
        topup_at = 100.0 / self._cfg.leverage - self._cfg.margin_topup_move_pct
        derisk_at = 100.0 / self._cfg.leverage - self._cfg.liquidation_buffer_pct
        breach = buffer_left <= derisk_at
        topup = buffer_left <= topup_at
        fired = breach or topup
        return Verdict(
            "R2-margin", fired,
            "derisk" if breach else ("topup" if topup else "none"),
            f"perp {move_pct:+.2f}% vs entry at {lev:.2f}x; liquidation "
            f"~{liq_move:.0f}%, buffer {buffer_left:.1f}pp "
            f"(topup below {topup_at:.0f}pp, derisk below {derisk_at:.0f}pp)",
            {"move_pct": move_pct, "buffer_pp": buffer_left, "leverage": lev,
             "topup_at_pp": topup_at, "derisk_at_pp": derisk_at})

    # ---- remediation sizing (2026-08-23) ----------------------------------
    def target_leverage(self, perp_entry: float, perp_now: float) -> float:
        """Effective leverage that restores a comfortable buffer.

        Buffer = 100/L - move. Solving for the leverage that puts the buffer
        back at the TOP-UP line with a margin of safety.
        """
        move_pct = 100.0 * (perp_now - perp_entry) / perp_entry
        want_buffer = (100.0 / self._cfg.leverage
                       - self._cfg.margin_topup_move_pct
                       + self._cfg.remediation_headroom_pp)
        liq_needed = move_pct + want_buffer
        if liq_needed <= 0:
            return self._cfg.min_effective_leverage
        return max(self._cfg.min_effective_leverage, 100.0 / liq_needed)

    @staticmethod
    def neutral_perp_notional(notional_spot: float, spot_entry: float,
                              perp_entry: float, spot_now: float,
                              perp_now: float) -> float:
        """Perp notional (stated at ENTRY price, the unit this ledger stores)
        whose current mark value matches the spot leg's current mark value.

        delta = spot_qty*spot_now - perp_qty*perp_now, so neutrality wants
        perp_qty = spot_qty*spot_now/perp_now, and the ledger holds
        notional = qty * entry_price.
        """
        if not (spot_entry and perp_now):
            return 0.0
        spot_qty = notional_spot / spot_entry
        target_perp_qty = spot_qty * spot_now / perp_now
        return target_perp_qty * perp_entry
