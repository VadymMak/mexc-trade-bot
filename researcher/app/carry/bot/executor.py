"""Executors — paper today, live never (yet).

PaperExecutor prices both legs by walking the real carry_book_l2 book, so the
recorded entry is the executable one. LiveExecutor is a deliberate stub: the
seam exists so the paper record is evidence about the live bot, but there is no
order-placement code, no credential handling, and no exchange write path
anywhere in this package.

BOTH LEGS OR NEITHER. A carry with one leg filled is a naked directional
position, which is the one outcome this strategy must never produce. If either
leg cannot be priced, the open is abandoned and logged.
"""
from __future__ import annotations

import logging
import uuid

from .book import BookSource

logger = logging.getLogger(__name__)


class ExecutionResult:
    __slots__ = ("ok", "reason", "group_id", "spot_price", "perp_price",
                 "notional_usd", "entry_cost_usd", "spot_slip", "perp_slip")

    def __init__(self, ok: bool, reason: str = "") -> None:
        self.ok, self.reason = ok, reason
        self.group_id = ""
        self.spot_price = self.perp_price = 0.0
        self.notional_usd = self.entry_cost_usd = 0.0
        self.spot_slip = self.perp_slip = 0.0


class PaperExecutor:
    """Simulates the open by walking the live book. Places NO orders."""

    mode = "paper"
    places_real_orders = False

    def __init__(self, books: BookSource, cfg) -> None:
        self._books = books
        self._cfg = cfg

    async def open_carry(self, ex: str, sym: str, notional_usd: float
                         ) -> ExecutionResult:
        """Long spot (lifts the ask) + short perp (hits the bid)."""
        spot_ask = await self._books.latest_curve(ex, sym, "spot", "ask")
        perp_bid = await self._books.latest_curve(ex, sym, "perp", "bid")
        if spot_ask is None or perp_bid is None:
            return ExecutionResult(False, "no book for one or both legs")

        sp = spot_ask.vwap_price(notional_usd, "ask")
        pp = perp_bid.vwap_price(notional_usd, "bid")
        if sp is None or pp is None:
            # The book cannot absorb this size right now. Refuse rather than
            # pretend — a partial fill here is exactly the leg risk we forbid.
            return ExecutionResult(
                False, f"book cannot absorb ${notional_usd:,.0f} "
                       f"(spot={'ok' if sp else 'thin'}, perp={'ok' if pp else 'thin'})")

        s_slip = spot_ask.slip_bps(notional_usd) or 0.0
        p_slip = perp_bid.slip_bps(notional_usd) or 0.0
        maker = self._cfg.maker_bps[ex]
        # entry cost = both legs' impact + both legs' maker fee, on notional
        entry_cost = notional_usd * ((s_slip + p_slip + 2 * maker) / 1e4)

        r = ExecutionResult(True)
        r.group_id = f"{ex}-{sym}-{uuid.uuid4().hex[:8]}"
        r.spot_price, r.perp_price = sp, pp
        r.notional_usd = notional_usd
        r.entry_cost_usd = entry_cost
        r.spot_slip, r.perp_slip = s_slip, p_slip
        return r

    async def trade_leg(self, ex: str, sym: str, market: str, usd: float,
                        direction: str) -> tuple[float, float, str]:
        """Price ONE leg's adjustment against the live book.

        Used by the remediation handlers (rebalance / derisk). `direction` is
        'buy' (lifts the ask) or 'sell' (hits the bid). Returns
        (fill_price, cost_usd, note). A remediation is a trade and is charged
        like one — otherwise the paper record would show risk management as
        free, which is precisely the illusion that makes a backtest lie.
        """
        usd = abs(usd)
        if usd <= 0:
            return 0.0, 0.0, "nothing to trade"
        side = "ask" if direction == "buy" else "bid"
        curve = await self._books.latest_curve(ex, sym, market, side)
        maker = self._cfg.maker_bps[ex]
        if curve is None:
            taker = self._cfg.taker_bps[ex]
            cost = usd * ((self._cfg.max_rt_slip_bps + taker) / 1e4)
            return 0.0, cost, f"no {market} {side} book — charged sweep cost"
        slip = curve.slip_bps(usd)
        if slip is None:
            taker = self._cfg.taker_bps[ex]
            cost = usd * ((self._cfg.max_rt_slip_bps + taker) / 1e4)
            return curve.touch, cost, f"{market} {side} book too thin — sweep cost"
        price = curve.vwap_price(usd, side) or curve.touch
        return price, usd * ((slip + maker) / 1e4), \
            f"{market} {direction} ${usd:,.0f} @ {price:.6g} ({slip:.1f}bps slip)"

    async def close_carry(self, ex: str, sym: str, notional_usd: float
                          ) -> tuple[float, str]:
        """Returns (exit_cost_usd, note). Exit sells spot, covers perp."""
        spot_bid = await self._books.latest_curve(ex, sym, "spot", "bid")
        perp_ask = await self._books.latest_curve(ex, sym, "perp", "ask")
        maker = self._cfg.maker_bps[ex]
        s = spot_bid.slip_bps(notional_usd) if spot_bid else None
        p = perp_ask.slip_bps(notional_usd) if perp_ask else None
        if s is None or p is None:
            # Exit into a book too thin to absorb us: charge the taker cost of
            # sweeping what is there. This is the honest pessimistic case.
            taker = self._cfg.taker_bps[ex]
            cost = notional_usd * ((self._cfg.max_rt_slip_bps + 2 * taker) / 1e4)
            return cost, "exit book too thin — charged sweep cost"
        return notional_usd * ((s + p + 2 * maker) / 1e4), "modelled maker exit"


class LiveExecutor:
    """NOT IMPLEMENTED — the seam for live trading, deliberately inert.

    Implementing this means: real maker order placement with post-only, fill
    reconciliation, margin queries, one-leg-failure unwind, and credentials.
    None of that exists. Construction alone raises, so a misconfiguration
    cannot silently degrade into a half-built live path.
    """

    mode = "live"
    places_real_orders = True

    def __init__(self, *a, **k) -> None:
        raise NotImplementedError(
            "LiveExecutor is a stub. Live trading requires order placement, "
            "fill reconciliation, margin management and one-leg-failure unwind "
            "— none of which are implemented. See CARRY_BOT_DESIGN.md §3.")


def build_executor(books: BookSource, cfg):
    """The paper->live switch. Refuses to arm live without both locks AND an
    implementation."""
    if cfg.is_live:
        return LiveExecutor(books, cfg)          # raises, by design
    if cfg.mode == "live" and not cfg.allow_live:
        logger.warning("[carry/bot] CARRY_BOT_MODE=live but CARRY_BOT_ALLOW_LIVE "
                       "is not set — running PAPER")
    return PaperExecutor(books, cfg)
