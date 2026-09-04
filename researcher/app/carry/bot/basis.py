"""The carry position's SECOND P&L leg — the spot/perp basis.

A long-spot / short-perp carry earns two things, not one:

    1. funding, every settlement epoch   -> booked since day one
    2. the change in the spot-perp BASIS between entry and exit

Until 2026-09-04 only (1) was booked. `close_price` was NULL on every closed
leg, so no price P&L of any kind existed and `paper_pnl_usd` was an identity:
funding minus modelled costs, reconciling to the cent by construction. Every
figure denominated in it — the cost/income ratio, the weight-cap table, the R7
valuation — inherited that blind spot.

CONVENTION: MID-TO-MID, COSTS SEPARATE
--------------------------------------
The basis leg is marked from the MID basis at entry to the MID basis at exit,
and `entry_cost_usd` / `exit_cost_usd` remain the explicit charge for crossing
the spread. The alternative (fill-to-fill, costs folded into the basis term)
was rejected: `executor.open_carry` fills spot at the ask VWAP and perp at the
bid VWAP, so a fill-to-fill basis already contains the whole round-trip spread
that `entry_cost_usd` then charges AGAIN. Measured over the first window, the
recorded entry basis sat below the contemporaneous mid basis in 14 of 14
positions, median -23.5 bps; the naive reconstruction gave -$6.05 against
+$0.27 mid-to-mid. Fill-to-fill would have welded that double-count into the
P&L permanently instead of leaving it in a one-off reconstruction.

Mid-to-mid also keeps execution cost visible as its own line, which is the
quantity we actually want to watch.

THE MARK IS A TRAILING MEDIAN, NOT A POINT READ
-----------------------------------------------
Intraday basis SD on these names is 9-59 bps against moves of interest of
20-40 bps: a single observation at entry and a single one at exit is not a
measurement. The mark is therefore the MEDIAN mid basis over a trailing
window (default 2 h). Trailing, not centred, because at entry a live bot has
no forward half — and the entry and exit marks must be the SAME estimator or
their difference measures the estimator, not the market.

The identical estimator is used for live marks and for backfilled ones, so the
two differ only in when they were computed. `basis_mark_source` says which.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Median MID basis over a trailing window, as of `at` (NULL => now()).
#
# The mid basis is computed from the QUOTES (perp_bid/ask, spot_bid/ask) when
# both sides are present, and falls back to the collector's mark-vs-spot
# `basis_bps` when they are not. Quotes first: `basis_bps` is (perp_mark -
# spot)/spot, and a mark is not a mid.
_MARK_SQL = """
WITH w AS (
  SELECT CASE
           WHEN perp_bid > 0 AND perp_ask > perp_bid
            AND spot_bid > 0 AND spot_ask > spot_bid
           THEN (((perp_bid + perp_ask) / 2.0) - ((spot_bid + spot_ask) / 2.0))
                / ((spot_bid + spot_ask) / 2.0) * 10000.0
           ELSE basis_bps
         END AS mid_bps,
         ts,
         (perp_bid > 0 AND perp_ask > perp_bid
          AND spot_bid > 0 AND spot_ask > spot_bid) AS from_quotes
  FROM funding_basis_snapshots
  WHERE exchange = $1 AND symbol = $2
        AND ts <= coalesce($3::timestamptz, now())
        AND ts >  coalesce($3::timestamptz, now()) - ($4 || ' hours')::interval
        AND (basis_bps IS NOT NULL OR (perp_bid > 0 AND spot_bid > 0))
)
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY mid_bps) AS mid_bps,
       count(*)                                             AS n,
       count(*) FILTER (WHERE from_quotes)                  AS n_quotes,
       max(ts)                                              AS last_ts
FROM w WHERE mid_bps IS NOT NULL
"""


class BasisMark:
    """A booked basis mark, with the inputs that produced it beside it."""

    __slots__ = ("bps", "n", "n_quotes", "last_ts", "window_h", "source")

    def __init__(self, bps, n, n_quotes, last_ts, window_h, source):
        self.bps = bps
        self.n = n
        self.n_quotes = n_quotes
        self.last_ts = last_ts
        self.window_h = window_h
        self.source = source

    @property
    def ok(self) -> bool:
        return self.bps is not None and self.n > 0

    def __repr__(self):
        if not self.ok:
            return f"<BasisMark UNMARKED ({self.source})>"
        return (f"<BasisMark {self.bps:+.1f}bps n={self.n} "
                f"({self.n_quotes} from quotes) {self.source}>")


async def mark(pool, ex: str, sym: str, at=None, window_h: float = 2.0,
               source: str = "live") -> BasisMark:
    """Median mid basis over the `window_h` hours ENDING at `at` (default now).

    Returns an unmarked BasisMark rather than raising or guessing when the
    window holds no usable observation. An unmarked leg books NO basis P&L and
    is flagged as such — a missing mark must never silently become 0 bps, which
    would book the position's whole basis move as zero and look like a
    measurement.
    """
    row = await pool.fetchrow(_MARK_SQL, ex, sym, at, str(window_h))
    if not row or not row["n"] or row["mid_bps"] is None:
        return BasisMark(None, 0, 0, None, window_h, f"{source}-unmarked")
    return BasisMark(float(row["mid_bps"]), int(row["n"]),
                     int(row["n_quotes"] or 0), row["last_ts"],
                     window_h, f"{source}-median{window_h:g}h")


def basis_pnl_usd(notional_usd: float, entry_bps: float | None,
                  exit_bps: float | None) -> float:
    """P&L of the basis leg for LONG SPOT + SHORT PERP, in USD.

        basis_bps = (perp - spot) / spot * 1e4

    so the basis NARROWING is a gain: the short perp is bought back cheaper
    relative to the spot we are long. This sign is the one the adverse-exit
    hypothesis got backwards.

    Both marks must exist. One-sided marking is not a measurement of a change,
    and returning 0.0 for it would be indistinguishable from a real flat move.
    """
    if entry_bps is None or exit_bps is None:
        return 0.0
    return notional_usd * (entry_bps - exit_bps) / 1e4


def carry_pnl_usd(notional_usd: float, realised_funding_usd: float,
                  entry_cost_usd: float, exit_cost_usd: float,
                  remediation_cost_usd: float,
                  entry_bps: float | None, exit_bps: float | None
                  ) -> tuple[float, float, float]:
    """The whole position P&L, decomposed. Returns
    (total, funding_only, basis) where

        funding_only = realised funding - every modelled cost   (the OLD series)
        basis        = mid-to-mid basis move                    (the NEW leg)
        total        = funding_only + basis

    `entry_cost_usd` and `exit_cost_usd` appear here EXACTLY ONCE, and the
    basis term is computed from MID marks that do not contain them. That is the
    whole of the no-double-count rule, and `tests/test_basis_booking.py`
    fails if it is violated.
    """
    funding_only = (realised_funding_usd - entry_cost_usd - exit_cost_usd
                    - remediation_cost_usd)
    basis = basis_pnl_usd(notional_usd, entry_bps, exit_bps)
    return funding_only + basis, funding_only, basis
