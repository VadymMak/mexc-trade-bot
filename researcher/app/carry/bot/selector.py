"""Selector — picks the carry basket from live Postgres data.

Every gate in CARRY_BOT_DESIGN.md §1(a) is implemented here. Nothing is
hardcoded from the Phase-2 study: the bot re-derives funding, spreads, basis
and depth from the tables each cycle, so it keeps working as the market moves.

THE ONE THING IT REFUSES TO TRUST is `funding_basis_snapshots.funding_interval_hours`
(8 in every row, a collector hardcode). Intervals come from IntervalResolver.
"""
from __future__ import annotations

import logging

from .book import (BookSource, LEGS, max_prudent_notional, worst_hour_capacity)

logger = logging.getLogger(__name__)

# One funding observation per REAL epoch (the $3 grid), newest sample in each.
_FUNDING_SQL = """
WITH ep AS (
  SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$3))::bigint)
         ts, funding_rate, (floor(extract(epoch FROM ts)/$3))::bigint AS e
  FROM funding_basis_snapshots
  WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
        AND ts > now() - ($4 || ' days')::interval
  ORDER BY e, ts DESC)
SELECT count(*) AS n, avg(funding_rate) AS mean_r, stddev_pop(funding_rate) AS sd_r,
       min(funding_rate) AS min_r,
       100.0*count(*) FILTER (WHERE funding_rate > 0)/nullif(count(*),0) AS pos_pct
FROM ep
"""

_QUOTE_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY perp_spread_bps) AS perp_spr,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY spot_spread_bps) AS spot_spr,
       avg(basis_bps)    AS basis_mean,
       stddev_pop(basis_bps) AS basis_sd,
       max(ts)           AS last_ts,
       count(*)          AS n
FROM funding_basis_snapshots
WHERE exchange=$1 AND symbol=$2 AND ts > now() - ($3 || ' days')::interval
      AND perp_bid > 0 AND perp_ask > perp_bid
      AND spot_bid > 0 AND spot_ask > spot_bid
"""

# THE SAME trailing window RiskManager.funding_flip exits on. Entry and exit
# MUST read one metric: when entry used the 14-day mean and exit used the
# trailing-7, a name could fail the exit test and pass the entry test in the
# same hour. That is the whole TUT churn bug (23 round-trips in 48h).
_TRAILING_SQL = """
WITH ep AS (
  SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$3))::bigint)
         (floor(extract(epoch FROM ts)/$3))::bigint AS e, funding_rate
  FROM funding_basis_snapshots
  WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
        AND ts > now() - interval '3 days'
  ORDER BY e, ts DESC)
SELECT funding_rate FROM ep ORDER BY e DESC LIMIT 7
"""

# Only consider names we actually have depth for — no book, no honest entry.
_UNIVERSE_SQL = """
SELECT DISTINCT exchange, symbol FROM carry_book_l2
WHERE ts > now() - ($1 || ' hours')::interval
ORDER BY 1, 2
"""


class Candidate:
    __slots__ = ("ex", "sym", "iv", "gross_apr", "mean_r", "pos_pct", "n_epochs",
                 "perp_spr", "spot_spr", "basis_mean", "basis_sd", "max_notional",
                 "slip_bps", "net_apr", "depth_usd", "depth_basis", "reject",
                 "apr7_cap", "apr3_cap", "neg_recent", "payback_days", "bar_apr")

    def __init__(self, ex, sym):
        self.ex, self.sym = ex, sym
        self.reject = None
        self.net_apr = float("nan")
        self.max_notional = 0.0
        self.apr7_cap = self.apr3_cap = float("nan")
        self.payback_days = float("nan")
        self.bar_apr = float("nan")
        self.neg_recent = 0

    @property
    def key(self):
        return (self.ex, self.sym)

    def __repr__(self):
        return (f"<{self.ex}/{self.sym} apr={self.net_apr:.1f}% "
                f"size=${self.max_notional:,.0f}>")


class Selector:
    def __init__(self, pool, cfg, intervals, books: BookSource, store) -> None:
        self._pool = pool
        self._cfg = cfg
        self._iv = intervals
        self._books = books
        self._store = store
        # Refreshed once per select() so every candidate sees one consistent view.
        self._blocks: dict = {}
        self._recent_exits: set = set()

    async def universe(self) -> list[tuple[str, str]]:
        rows = await self._pool.fetch(_UNIVERSE_SQL,
                                      str(self._cfg.depth_lookback_hours))
        return [(r["exchange"], r["symbol"]) for r in rows]

    def _net_apr(self, ex: str, gross: float, slip_bps: float, hold_days: int) -> float:
        """Net APR on DEPLOYED CAPITAL: maker fees + book-walk slippage,
        amortised over the hold, divided by the capital multiple."""
        fee = 4 * self._cfg.maker_bps[ex]
        rt = fee + slip_bps
        return (gross - rt * (365.0 / hold_days) / 100.0) / self._cfg.capital_multiple

    async def _trailing(self, ex: str, sym: str, iv: float) -> tuple:
        """(apr7_on_capital, apr3_on_capital, negative_epoch_count).

        Deliberately the same computation as RiskManager.funding_flip so the
        two gates cannot disagree about what a name is currently earning.
        """
        rows = await self._pool.fetch(_TRAILING_SQL, ex, sym, int(iv * 3600))
        rates = [float(r["funding_rate"]) for r in rows]
        if not rates:
            return float("nan"), float("nan"), 0
        ann = (24.0 / iv) * 365.0 * 100.0 / self._cfg.capital_multiple
        apr7 = (sum(rates) / len(rates)) * ann
        head = rates[:3]
        apr3 = (sum(head) / len(head)) * ann
        return apr7, apr3, sum(1 for r in rates if r < 0)

    def _payback_days(self, ex: str, gross_apr: float, slip_bps: float) -> float:
        """Days of funding needed to repay the round trip. inf if it never does."""
        if gross_apr <= 0:
            return float("inf")
        rt_bps = 4 * self._cfg.maker_bps[ex] + slip_bps
        return (rt_bps / 10000.0) / (gross_apr / 100.0 / 365.0)

    async def evaluate(self, ex: str, sym: str) -> Candidate:
        c = Candidate(ex, sym)
        cfg = self._cfg

        # --- G0: hard cooldown. A name we just flip-exited is exiled, full
        #     stop — no APR is good enough to re-open it inside the window.
        blk = self._blocks.get((ex, sym))
        if blk:
            c.reject = f"cooldown={blk['hours_left']:.0f}h"
            return c

        # --- corrected funding interval (never the stored column) ----------
        iv = await self._iv.get(ex, sym)
        if iv is None:
            c.reject = "interval-unresolved"
            return c
        c.iv = iv

        f = await self._pool.fetchrow(_FUNDING_SQL, ex, sym, int(iv * 3600),
                                      str(cfg.lookback_days))
        if not f or not f["n"] or f["n"] < cfg.min_epochs:
            c.reject = f"epochs={f['n'] if f else 0}<{cfg.min_epochs}"
            return c
        c.n_epochs = f["n"]
        c.mean_r = float(f["mean_r"] or 0.0)
        c.pos_pct = float(f["pos_pct"] or 0.0)
        c.gross_apr = c.mean_r * (24.0 / iv) * 365.0 * 100.0

        if c.pos_pct < cfg.min_positive_frac * 100.0:
            c.reject = f"pos={c.pos_pct:.0f}%<{cfg.min_positive_frac:.0%}"
            return c
        if c.gross_apr <= 0:
            c.reject = "gross<=0"
            return c

        # --- G1: TRAILING gate — the reconciliation with R4 ------------------
        # Every name must clear the exit floor on the SAME trailing-7 metric R4
        # exits on, so a name that would immediately fail the exit test can
        # never pass entry. A name inside the hysteresis memory faces the
        # HIGHER re-entry bar, and must clear it on trailing-3 as well: one fat
        # epoch must not be enough to buy its way back in.
        c.apr7_cap, c.apr3_cap, c.neg_recent = await self._trailing(ex, sym, iv)
        recent = (ex, sym) in self._recent_exits
        c.bar_apr = cfg.reentry_apr if recent else cfg.min_hold_apr
        if not (c.apr7_cap == c.apr7_cap):            # NaN => no trailing data
            c.reject = "trail=no-epochs"
            return c
        if c.apr7_cap < c.bar_apr:
            c.reject = f"trail7={c.apr7_cap:.1f}%<{c.bar_apr:.1f}%"
            return c
        if recent:
            if c.apr3_cap < c.bar_apr:
                c.reject = f"trail3={c.apr3_cap:.1f}%<{c.bar_apr:.1f}%"
                return c
            if c.neg_recent:
                c.reject = f"neg-epochs={c.neg_recent}"
                return c

        q = await self._pool.fetchrow(_QUOTE_SQL, ex, sym, str(cfg.lookback_days))
        if not q or not q["n"]:
            c.reject = "no-spot-quotes"          # spot leg must exist
            return c
        c.perp_spr = float(q["perp_spr"] or 0.0)
        c.spot_spr = float(q["spot_spr"] or 0.0)
        c.basis_mean = float(q["basis_mean"] or 0.0)
        c.basis_sd = float(q["basis_sd"] or 0.0)
        if c.perp_spr + c.spot_spr > cfg.max_rt_spread_bps:
            c.reject = f"spread={c.perp_spr + c.spot_spr:.0f}bps"
            return c
        if abs(c.basis_mean) > cfg.max_basis_bps:
            c.reject = f"basis={c.basis_mean:.0f}bps"
            return c

        # --- worst-hour depth: R6, the size cap ---------------------------
        notional, slip = await max_prudent_notional(self._books, ex, sym, cfg)
        if notional < cfg.min_notional_usd:
            c.reject = f"depth<${cfg.min_notional_usd:.0f}"
            return c
        c.max_notional = notional
        c.slip_bps = slip

        by_hod = await self._books.leg_curves(ex, sym, "spot", "bid")
        c.depth_usd, _, c.depth_basis = worst_hour_capacity(
            by_hod, cfg.max_rt_slip_bps, cfg)

        # --- G2: COST GATE — must earn back its own round trip ---------------
        # Not "is the APR nice", but "does the expected funding over the
        # minimum hold comfortably exceed what it costs to get in and out".
        # TUT paid $8.26 in round trips to collect $0.11; a position that
        # cannot repay its entry has no business being opened.
        c.payback_days = self._payback_days(ex, c.gross_apr, slip)
        if c.payback_days > cfg.max_payback_days:
            c.reject = (f"payback={c.payback_days:.1f}d>"
                        f"{cfg.max_payback_days:.1f}d")
            return c

        c.net_apr = self._net_apr(ex, c.gross_apr, slip, cfg.hold_days)
        if c.net_apr < cfg.min_net_apr:
            c.reject = f"net={c.net_apr:.1f}%<{cfg.min_net_apr:.1f}%"
            return c
        return c

    async def select(self) -> tuple[list[Candidate], list[Candidate]]:
        """Returns (chosen, all_evaluated)."""
        # One consistent snapshot of the exile list for the whole pass.
        self._blocks = await self._store.reentry_blocks()
        self._recent_exits = await self._store.recently_exited(
            self._cfg.reentry_memory_days)
        if self._blocks:
            logger.info("[carry/bot][select] %d name(s) in re-entry cooldown: %s",
                        len(self._blocks),
                        ", ".join(f"{ex}/{sym} {b['hours_left']:.0f}h"
                                  for (ex, sym), b in sorted(self._blocks.items())))
        cands = []
        for ex, sym in await self.universe():
            try:
                cands.append(await self.evaluate(ex, sym))
            except Exception as exc:
                logger.warning("[carry/bot] evaluate %s/%s failed: %r", ex, sym, exc)
        ok = [c for c in cands if c.reject is None]
        ok.sort(key=lambda c: -c.net_apr)
        return ok, cands

    def allocate(self, ranked: list[Candidate], capital_usd: float,
                 already: dict[tuple[str, str], float] | None = None
                 ) -> list[tuple[Candidate, float]]:
        """Greedy fill under the per-name cap and the MEXC venue cap (R7).

        The venue cap is a share of DEPLOYED capital, and capping MEXC shrinks
        deployment, which shrinks the MEXC budget again — so iterate to the
        fixpoint rather than capping against the target.
        """
        cfg = self._cfg
        already = already or {}
        deployed_guess = capital_usd
        out: list[tuple[Candidate, float]] = []
        for _ in range(24):
            out, spent, mexc = [], 0.0, 0.0
            budget_mexc = cfg.mexc_venue_cap * deployed_guess
            for c in ranked:
                if len(out) + len(already) >= cfg.max_positions:
                    break
                room_cap = c.max_notional * cfg.capital_multiple - already.get(c.key, 0.0)
                room = min(room_cap, capital_usd - spent)
                if c.ex == "mexc":
                    room = min(room, budget_mexc - mexc)
                if room <= cfg.min_notional_usd * cfg.capital_multiple:
                    continue
                out.append((c, room))
                spent += room
                if c.ex == "mexc":
                    mexc += room
                if spent >= capital_usd - 1.0:
                    break
            if abs(spent - deployed_guess) < 1.0:
                break
            deployed_guess = spent
        return out
