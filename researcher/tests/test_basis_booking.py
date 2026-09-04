#!/usr/bin/env python3
"""Tests for the carry basis leg — and specifically for the ONE WAY IT CAN LIE.

    usage:  researcher/.venv/bin/python tests/test_basis_booking.py

The reconciliation this replaces ("paper_pnl ties to the cent") was an
IDENTITY: paper_pnl_usd was computed as funding-minus-costs and then checked
against funding-minus-costs, so it could not fail and proved nothing. Every
assertion below is constructed so that a plausible wrong implementation makes
it FAIL, and `test_double_count_is_detectable` demonstrates that directly by
running the wrong implementation alongside the right one.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.carry.bot import basis                                   # noqa: E402
from app.carry.bot.config import CarryBotConfig                   # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# A scenario with a PERFECTLY FLAT market. The mid basis is identical at entry
# and at exit, so the true basis P&L is exactly zero and the whole P&L is
# funding minus the round trip. Every fill, however, crosses the spread.
# --------------------------------------------------------------------------
N = 10_000.0            # spot notional, USD
MID_BPS = 40.0          # mid basis, unchanged entry -> exit
HALF_SPREAD_BPS = 10.0  # per leg, per side
RT_BPS = 4 * HALF_SPREAD_BPS          # both legs, both sides = 40 bps
FUNDING = 50.0
ENTRY_COST = N * (2 * HALF_SPREAD_BPS) / 1e4     # $20 — in at ask/bid
EXIT_COST = N * (2 * HALF_SPREAD_BPS) / 1e4      # $20 — out at bid/ask

# What the executor RECORDS as entry_price/close_price. Long spot lifts the
# ask and short perp hits the bid, so the fill basis is depressed by the
# entry half-spread; the exit reverses both, so it is inflated by the exit
# half-spread. This is executor.py:47-54 and :115-121.
FILL_ENTRY_BPS = MID_BPS - 2 * HALF_SPREAD_BPS   # +20
FILL_EXIT_BPS = MID_BPS + 2 * HALF_SPREAD_BPS    # +60


def wrong_fill_to_fill(notional, funding, entry_cost, exit_cost) -> float:
    """The trap: book close_price - entry_price as the basis term while ALSO
    charging entry_cost_usd and exit_cost_usd as separate lines."""
    bad_basis = notional * (FILL_ENTRY_BPS - FILL_EXIT_BPS) / 1e4
    return funding - entry_cost - exit_cost + bad_basis


def test_flat_market_books_no_basis_pnl():
    total, funding_only, b = basis.carry_pnl_usd(
        N, FUNDING, ENTRY_COST, EXIT_COST, 0.0, MID_BPS, MID_BPS)
    check("flat market books ZERO basis P&L", abs(b) < 1e-12, f"basis=${b:.6f}")
    check("flat market total is funding minus ONE round trip",
          abs(total - (FUNDING - ENTRY_COST - EXIT_COST)) < 1e-9,
          f"${total:.4f} vs ${FUNDING - ENTRY_COST - EXIT_COST:.4f}")
    check("total decomposes exactly into its two legs",
          abs(total - (funding_only + b)) < 1e-12)


def test_double_count_is_detectable():
    """THE POINT OF THIS FILE. A fill-to-fill basis term charges the round trip
    a SECOND time. The gap between the two implementations must equal the round
    trip exactly — that is what 'charged twice' means, stated so it can fail."""
    right, _, _ = basis.carry_pnl_usd(
        N, FUNDING, ENTRY_COST, EXIT_COST, 0.0, MID_BPS, MID_BPS)
    wrong = wrong_fill_to_fill(N, FUNDING, ENTRY_COST, EXIT_COST)
    gap = right - wrong
    check("fill-to-fill differs from mid-to-mid (the check CAN fail)",
          abs(gap) > 1e-9, f"right=${right:.4f} wrong=${wrong:.4f}")
    check("the gap is EXACTLY one extra round trip — the double count",
          abs(gap - (ENTRY_COST + EXIT_COST)) < 1e-9,
          f"gap=${gap:.4f} round-trip=${ENTRY_COST + EXIT_COST:.4f}")
    check("the engine's booked basis is NOT the fill-to-fill figure",
          abs(basis.basis_pnl_usd(N, MID_BPS, MID_BPS)
              - N * (FILL_ENTRY_BPS - FILL_EXIT_BPS) / 1e4) > 1e-9)


def test_sign_basis_compression_is_a_gain():
    """The retraction, encoded. For LONG SPOT / SHORT PERP a basis that
    COMPRESSES is a GAIN: the short perp is bought back cheaper relative to the
    spot we are long. The adverse-exit hypothesis had this backwards."""
    gain = basis.basis_pnl_usd(N, 80.0, 20.0)     # basis compresses 80 -> 20
    loss = basis.basis_pnl_usd(N, 20.0, 80.0)     # basis widens   20 -> 80
    check("basis compression is a GAIN", gain > 0, f"${gain:+.2f}")
    check("basis widening is a LOSS", loss < 0, f"${loss:+.2f}")
    check("the two are symmetric", abs(gain + loss) < 1e-12)


def test_unmarked_leg_books_nothing():
    """A missing mark must never silently become 0 bps — that would book the
    position's entire basis move as 'flat' and be indistinguishable from a real
    measurement of no move."""
    check("missing exit mark books no basis P&L",
          basis.basis_pnl_usd(N, 40.0, None) == 0.0)
    check("missing entry mark books no basis P&L",
          basis.basis_pnl_usd(N, None, 40.0) == 0.0)


def test_r4_floor_is_interval_aware():
    """The universal venue default (5e-05/epoch) is a REST VALUE carrying no
    information about the name. Under the old flat annual floor it sat at 91%
    of the floor for a 4 h name but only 46% for an 8 h name, so 8 h names were
    exited by construction. After the fix its position must be IDENTICAL at
    every interval — that is what 'compare like with like' means."""
    c = CarryBotConfig()
    default = 5e-05
    ratios = {}
    for iv in (1.0, 2.0, 4.0, 8.0):
        apr_cap = default * (24.0 / iv) * 365.0 * 100.0 / c.capital_multiple
        ratios[iv] = apr_cap / c.hold_apr_floor(iv)
    spread = max(ratios.values()) - min(ratios.values())
    check("the venue default sits at the SAME place at every interval",
          spread < 1e-9, "ratios " + ", ".join(f"{k:.0f}h={v:.3f}" for k, v in ratios.items()))
    check("the 4 h anchor is unchanged at the configured floor",
          abs(c.hold_apr_floor(4.0) - c.min_hold_apr) < 1e-9,
          f"{c.hold_apr_floor(4.0):.2f}%")
    check("the 8 h floor is half the 4 h floor",
          abs(c.hold_apr_floor(8.0) * 2 - c.hold_apr_floor(4.0)) < 1e-9,
          f"8h={c.hold_apr_floor(8.0):.2f}% 4h={c.hold_apr_floor(4.0):.2f}%")
    check("re-entry stays strictly above the exit floor (hysteresis intact)",
          all(c.reentry_apr_floor(iv) > c.hold_apr_floor(iv) for iv in (4.0, 8.0)))
    # An OLD-STYLE flat floor must fail the first assertion — proof it can.
    old = {iv: (default * (24.0 / iv) * 365.0 * 100.0 / c.capital_multiple)
                / c.min_hold_apr for iv in (4.0, 8.0)}
    check("the flat floor it replaces WOULD fail that test",
          abs(old[4.0] - old[8.0]) > 0.1,
          f"flat: 4h={old[4.0]:.3f} 8h={old[8.0]:.3f} of floor")


# --------------------------------------------------------------------------
# The live database: the identity that is NOT an identity.
# --------------------------------------------------------------------------
async def test_database_reconciliation():
    try:
        import asyncpg
    except ImportError:
        print("  SKIP  database checks (asyncpg unavailable)")
        return
    dsn = os.getenv("CARRY_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        print("  SKIP  database checks (no CARRY_DSN/DATABASE_URL)")
        return
    conn = await asyncpg.connect(dsn)
    try:
        # 1. The decomposition invariant, on every row.
        bad = await conn.fetchval(
            """SELECT count(*) FROM paper_carry_positions
               WHERE abs(paper_pnl_usd
                         - (funding_only_pnl_usd + coalesce(basis_pnl_usd,0))) > 1e-9""")
        check("every leg satisfies paper_pnl = funding_only + basis",
              bad == 0, f"{bad} violating row(s)")

        # 2. TWO INDEPENDENT DERIVATIONS of the window total. The first is
        #    built from the RECEIPTS (funding accrued, costs charged) plus the
        #    stored basis marks; the second is the redefined paper_pnl column.
        #    They are computed from different columns by different arithmetic,
        #    so agreement is evidence rather than tautology.
        r = await conn.fetchrow(
            """SELECT
                 sum(realised_funding_usd) - sum(entry_cost_usd)
                   - sum(exit_cost_usd) - sum(coalesce(remediation_cost_usd,0))
                                                       AS receipts_funding_only,
                 sum(CASE WHEN leg='spot'
                            AND entry_basis_bps IS NOT NULL
                            AND exit_basis_bps  IS NOT NULL
                          THEN notional_usd
                               * (entry_basis_bps - exit_basis_bps) / 1e4
                          ELSE 0 END)                  AS receipts_basis,
                 sum(paper_pnl_usd)                    AS column_total,
                 sum(funding_only_pnl_usd)             AS column_funding_only,
                 sum(coalesce(basis_pnl_usd,0))        AS column_basis
               FROM paper_carry_positions""")
        recon = float(r["receipts_funding_only"]) + float(r["receipts_basis"])
        col = float(r["column_total"])
        check("receipts+marks agrees with the redefined paper_pnl",
              abs(recon - col) < 1e-6,
              f"receipts=${recon:+.6f} column=${col:+.6f} "
              f"diff=${recon - col:+.2e}")
        check("the basis term agrees derivation-to-column",
              abs(float(r["receipts_basis"]) - float(r["column_basis"])) < 1e-6,
              f"${float(r['receipts_basis']):+.4f}")
        print(f"    funding-only ${float(r['column_funding_only']):+.4f}  "
              f"basis ${float(r['column_basis']):+.4f}  "
              f"total ${col:+.4f}")

        # 3. No closed leg may carry an exit cost without its costs appearing
        #    exactly once in funding_only. This is the double count, in the DB.
        dbl = await conn.fetchval(
            """SELECT count(*) FROM paper_carry_positions
               WHERE status='closed'
                 AND abs(funding_only_pnl_usd
                         - (realised_funding_usd - entry_cost_usd - exit_cost_usd
                            - coalesce(remediation_cost_usd,0))) > 1e-9""")
        check("no closed leg charges its round trip twice", dbl == 0,
              f"{dbl} violating row(s)")
    finally:
        await conn.close()


def main() -> int:
    print("basis booking — mid-to-mid, costs separate")
    for t in (test_flat_market_books_no_basis_pnl,
              test_double_count_is_detectable,
              test_sign_basis_compression_is_a_gain,
              test_unmarked_leg_books_nothing,
              test_r4_floor_is_interval_aware):
        print(f"\n{t.__name__}:")
        t()
    print("\ntest_database_reconciliation:")
    asyncio.run(test_database_reconciliation())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
