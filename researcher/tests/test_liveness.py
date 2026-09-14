#!/usr/bin/env python3
"""Tests for strategy liveness — and for the bug it was written after.

    usage:  researcher/.venv/bin/python tests/test_liveness.py
            CARRY_DSN=... researcher/.venv/bin/python tests/test_liveness.py

THE RULE THIS OBEYS: a check that has never fired is a hypothesis. Every test
below drives the detector into the state it exists to catch and asserts that it
FIRES — and `test_close_group_untyped_param_is_detectable` reproduces the
original defect against a real database, so it fails on the old SQL and passes
on the new. The 2026-09 outage was invisible for 55+ hours precisely because
nothing ever exercised the failing path.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.carry.bot import liveness                                # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# A book like the live one: 7 positions, shortest settlement interval 4 h,
# selection every 60 min.
BOOK = dict(open_positions=7, min_interval_h=4.0, select_every_min=60.0)
H = 3600.0


def ev(**kw):
    base = dict(BOOK, accrual_age_s=600.0, select_age_s=600.0,
                consecutive_failures=0)
    base.update(kw)
    return liveness.evaluate(**base)


# --------------------------------------------------------------------------
def test_healthy_book_is_quiet():
    v = ev()
    check("a working strategy reports ok", v.severity == liveness.OK, v.detail)
    check("and does not fire", not v.fired)


def test_missed_funding_epoch_fires():
    """The threshold is 1.25x the SHORTEST interval in the book (4h -> 5h)."""
    v = ev(accrual_age_s=4.9 * H)
    check("just inside one settlement interval is still ok",
          v.severity == liveness.OK, f"4.9h: {v.detail}")
    v = ev(accrual_age_s=5.1 * H)
    check("a missed funding epoch FIRES", v.severity == liveness.STALLED,
          f"5.1h: {v.detail}")
    check("and says how many epochs were missed", "epoch(s) missed" in v.detail)


def test_threshold_follows_the_interval_not_a_clock():
    """An 8h book must tolerate 8h of silence; a 4h book must not."""
    slow = liveness.evaluate(**dict(BOOK, min_interval_h=8.0,
                                    accrual_age_s=6.0 * H,
                                    select_age_s=600.0, consecutive_failures=0))
    fast = ev(accrual_age_s=6.0 * H)
    check("6h of silence is ok for an 8h book", slow.severity == liveness.OK)
    check("the same 6h FIRES for a 4h book", fast.severity == liveness.STALLED)


def test_stalled_selection_fires():
    v = ev(select_age_s=179 * 60)
    check("under 3x the selection cadence is ok", v.severity == liveness.OK)
    v = ev(select_age_s=181 * 60)
    check("a stalled selection loop FIRES", v.severity == liveness.STALLED, v.detail)


def test_repeated_cycle_failure_fires():
    v = ev(consecutive_failures=2)
    check("two failures is noise", v.severity == liveness.OK)
    v = ev(consecutive_failures=3)
    check("three in a row FIRES", v.severity == liveness.STALLED, v.detail)


def test_empty_book_is_not_a_stall():
    v = liveness.evaluate(open_positions=0, min_interval_h=None,
                          accrual_age_s=None, select_age_s=600.0,
                          select_every_min=60.0, consecutive_failures=0)
    check("an empty book accrues nothing and is not a failure",
          v.severity == liveness.OK, v.detail)


def test_never_accrued_with_an_open_book_fires():
    v = ev(accrual_age_s=None)
    check("never having accrued on an OPEN book FIRES",
          v.severity == liveness.STALLED, v.detail)


# --------------------------------------------------------------------------
# THE REAL FAILURE, REPLAYED. A fake clock lets us stall the strategy exactly
# the way 2026-09-10 did — the cycle raises after the risk events are written
# and before the accrual is — and watch the detector go from quiet to loud.
# --------------------------------------------------------------------------
def test_replay_of_the_2026_09_10_stall():
    now = [0.0]
    marks = liveness.LivenessMarks(lambda: now[0])
    marks.seed(accrual_age_s=60.0, select_age_s=60.0)

    def verdict():
        return liveness.evaluate(
            **BOOK, accrual_age_s=marks.accrual_age_s,
            select_age_s=marks.select_age_s,
            consecutive_failures=marks.consecutive_failures)

    # Healthy: the cycle completes, both artefacts get stamped.
    for _ in range(10):
        now[0] += 60.0
        marks.accrual_written(); marks.select_completed(); marks.cycle_ok()
    check("replay: healthy loop is quiet", verdict().severity == liveness.OK)

    # 12:09:02 — check_risk raises. Risk events were already written; accrue()
    # and the selection pass are never reached. Nothing stamps the marks.
    fired_at = None
    for tick in range(1, 30 * 60):          # 30 h of one-minute ticks
        now[0] += 60.0
        marks.cycle_failed()                # the ONLY thing that still happens
        v = verdict()
        if v.fired and fired_at is None:
            fired_at = tick
    check("replay: the detector FIRES during the stall", fired_at is not None)
    check("replay: it fires within minutes, not hours",
          fired_at is not None and fired_at <= 5,
          f"fired on tick {fired_at}")
    final = verdict()
    check("replay: it names the missed epochs AND the repeated failures",
          "epoch(s) missed" in final.detail and "consecutive" in final.detail,
          final.detail)

    # 09-11 16:04 — the exit condition clears, the cycle completes again.
    now[0] += 60.0
    marks.accrual_written(); marks.select_completed(); marks.cycle_ok()
    check("replay: recovery is detected too",
          verdict().severity == liveness.OK)


def test_would_have_fired_on_2026_09_05():
    """The episode the first post-mortem mis-attributed to normal cadence.

    Measured from the receipts: accrual silent 04:00:44Z -> 10:17:37Z (6.28 h)
    and again 12:01:11Z -> 15:38:44Z (3.63 h); 374 `cycle failed` lines that
    day. The book then was 5 positions, shortest settlement interval 4 h, so
    the accrual limit is 5 h. Both sub-episodes must fire, and the second one
    must fire on the SELECTION and FAILURE arms even though it is under the
    accrual limit — otherwise a 3.6 h hole stays invisible.
    """
    book5 = dict(open_positions=5, min_interval_h=4.0, select_every_min=60.0)

    first = liveness.evaluate(**book5, accrual_age_s=6.28 * H,
                              select_age_s=6.28 * H, consecutive_failures=200)
    check("09-05 first hole (6.28h) FIRES", first.severity == liveness.STALLED,
          first.detail)
    check("09-05 first hole names the missed epoch",
          "epoch(s) missed" in first.detail)

    # The shorter hole: 3.63 h is UNDER the 5 h accrual limit, so the accrual
    # arm alone would miss it. Selection (3x60min=180min) and the failure
    # counter must carry it.
    second = liveness.evaluate(**book5, accrual_age_s=3.63 * H,
                               select_age_s=3.63 * H, consecutive_failures=150)
    check("09-05 second hole (3.63h) FIRES despite being under the accrual limit",
          second.severity == liveness.STALLED, second.detail)
    check("09-05 second hole fires on selection, not accrual",
          "selection pass" in second.detail and "epoch(s) missed" not in second.detail,
          second.detail)

    # And the earliest possible detection: 3 consecutive failures is ~3 min in.
    early = liveness.evaluate(**book5, accrual_age_s=180.0, select_age_s=180.0,
                              consecutive_failures=3)
    check("09-05 would have been caught ~3 minutes in, not 6 hours",
          early.severity == liveness.STALLED, early.detail)


def test_seeding_does_not_hide_a_stall_across_a_restart():
    now = [1000.0]
    marks = liveness.LivenessMarks(lambda: now[0])
    marks.seed(accrual_age_s=9 * H, select_age_s=9 * H)   # DB says 9h of silence
    v = liveness.evaluate(**BOOK, accrual_age_s=marks.accrual_age_s,
                          select_age_s=marks.select_age_s,
                          consecutive_failures=0)
    check("a restart mid-stall still reports the stall",
          v.severity == liveness.STALLED, v.detail)


# --------------------------------------------------------------------------
# THE ORIGINAL DEFECT, against a real database. `close_carry` returns
# (None, None) for the fills whenever the book curve is missing; with both arms
# of the CASE untyped, Postgres infers TEXT and the UPDATE raises against a
# double precision column. 2,554 exits failed this way.
# --------------------------------------------------------------------------
BROKEN = "UPDATE paper_carry_positions SET close_price = CASE WHEN leg='spot' THEN $1 ELSE $2 END WHERE false"
FIXED = ("UPDATE paper_carry_positions SET close_price = "
         "CASE WHEN leg='spot' THEN $1::double precision "
         "ELSE $2::double precision END WHERE false")


async def test_close_group_untyped_param_is_detectable():
    try:
        import asyncpg
    except ImportError:
        print("  SKIP  database checks (asyncpg unavailable)")
        return
    dsn = os.getenv("CARRY_DSN") or os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        import re as _re
        env = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env):
            m = _re.search(r"^NEON_DATABASE_URL=(.*)$", open(env).read(), _re.M)
            dsn = m.group(1).strip() if m else None
    if not dsn:
        print("  SKIP  database checks (no CARRY_DSN/NEON_DATABASE_URL)")
        return
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        # WHERE false => touches no row. This tests the PREPARE, which is where
        # the type inference happens and where the original failure occurred.
        broke = None
        try:
            await conn.execute(BROKEN, None, None)
            broke = False
        except asyncpg.exceptions.DatatypeMismatchError:
            broke = True
        check("the OLD untyped SQL still fails on NULL fills (the check can fail)",
              broke is True,
              "DatatypeMismatchError reproduced" if broke else
              "it did NOT fail — this test would prove nothing")

        ok = True
        try:
            await conn.execute(FIXED, None, None)
        except Exception as exc:                          # noqa: BLE001
            ok = False
            print(f"        {exc!r}")
        check("the FIXED cast SQL accepts NULL fills", ok)

        # and the real thing, as shipped
        from app.carry.bot import store as store_mod
        src = store_mod.__file__
        text = open(src, encoding="utf-8").read()
        check("the shipped close_group casts BOTH fill parameters",
              "$5::double precision" in text and "$6::double precision" in text,
              src)
    finally:
        await conn.close()


def main() -> int:
    print("strategy liveness — measured from the work, not from a heartbeat")
    for t in (test_healthy_book_is_quiet,
              test_missed_funding_epoch_fires,
              test_threshold_follows_the_interval_not_a_clock,
              test_stalled_selection_fires,
              test_repeated_cycle_failure_fires,
              test_empty_book_is_not_a_stall,
              test_never_accrued_with_an_open_book_fires,
              test_replay_of_the_2026_09_10_stall,
              test_would_have_fired_on_2026_09_05,
              test_seeding_does_not_hide_a_stall_across_a_restart):
        print(f"\n{t.__name__}")
        t()
    print("\ntest_close_group_untyped_param_is_detectable")
    asyncio.run(test_close_group_untyped_param_is_detectable())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
