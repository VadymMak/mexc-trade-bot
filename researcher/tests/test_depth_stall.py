#!/usr/bin/env python3
"""Depth-collector stall escalation — and a correction to the fix that preceded it.

    usage:  researcher/.venv/bin/python tests/test_depth_stall.py

WHAT THIS EXISTS FOR. On 2026-09-14 `carry-depth` gained a hard stall that
exited for a clean systemd restart when the freshest PERP socket went quiet.
Measured the same day over 24 h and 398,450 inter-snapshot intervals:

    perp  p50 2.01  p95 2.11  p999 2.84  max  3.9 min   0 gaps > 15 min
    spot  p50 2.25  p95 2.99  p999 11.25 max 22.5 min  30 gaps > 15 min

**The escalation watched the leg that does not fail and ignored the one that
does.** Every observed stall was on the spot REST sweep, whose class docstring
asserted "REST needs no watchdog". That is the project's standing defect class
once more: a check reporting on something other than the thing it is believed
to check.

Spot gets LOOSER limits than perp on purpose — it is a 153-symbol REST sweep,
not a socket, so applying perp's 900 s to it would have exited the collector
nine times in one day for stalls that self-recovered, taking healthy perp
sockets down with it.

AND IT TESTS THE PATH, NOT THE CONSTRUCTOR. Generations 21-24 of the carry bot
died because a smoke test checked that an object could be built rather than
that the new code could run. `test_successful_poll_stamps_last_ok` therefore
drives a real poll through `SpotDepthPoller._one` and checks the stamp moved.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.carry.depth_main import evaluate_stall                   # noqa: E402
from app.carry import depth_collectors as dc                      # noqa: E402

PASS, FAIL = [], []
MIN = 60.0


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# Measured 2026-09-14, 24 h.
PERP_WORST = 3.9 * MIN
SPOT_WORST = 22.5 * MIN
SPOT_P999 = 11.25 * MIN


def test_measured_reality_does_not_thrash():
    a, _ = evaluate_stall([PERP_WORST], SPOT_P999)
    check("spot at its p999 (11.2min) does not escalate", a == "ok", a)
    a, r = evaluate_stall([PERP_WORST], SPOT_WORST)
    check("spot at its measured WORST (22.5min) warns but does not exit",
          a == "soft", f"{a}: {r}")
    check("and perp at its measured worst (3.9min) is silent",
          evaluate_stall([PERP_WORST], 60.0)[0] == "ok")


def test_the_old_perp_only_rule_MISSED_the_real_stall():
    """The check that can fail: the shipped-then-corrected logic."""
    def perp_only(perp_ages, spot_age):
        freshest = min((x for x in perp_ages if x >= 0), default=1e9)
        return "hard" if freshest > 900.0 else ("soft" if freshest > 300.0 else "ok")

    old = perp_only([PERP_WORST], SPOT_WORST)
    new, reasons = evaluate_stall([PERP_WORST], SPOT_WORST)
    check("the OLD perp-only rule saw nothing during the real 22.5min stall",
          old == "ok", f"old={old}")
    check("the NEW rule catches it", new == "soft" and any("spot" in x for x in reasons),
          f"new={new}: {reasons}")


def test_each_side_escalates_on_its_own_break():
    check("a dead perp socket (20min) HARD stalls",
          evaluate_stall([20 * MIN], 60.0)[0] == "hard")
    check("a dead spot sweep (50min) HARD stalls",
          evaluate_stall([60.0], 50 * MIN)[0] == "hard")
    check("perp is judged on its FRESHEST socket, not its worst",
          evaluate_stall([60.0, 20 * MIN], 60.0)[0] == "ok",
          "one live socket keeps the curves fresh")
    check("all perp sockets dead HARD stalls",
          evaluate_stall([20 * MIN, 25 * MIN], 60.0)[0] == "hard")


def test_never_seen_is_not_healthy_for_perp_but_is_skipped_for_spot():
    check("no perp socket has ever spoken -> hard",
          evaluate_stall([-1.0], 60.0)[0] == "hard")
    check("spot never-seen is not asserted on (startup)",
          evaluate_stall([60.0], -1.0)[0] == "ok")


# --------------------------------------------------------------------------
# THE PATH, NOT THE CONSTRUCTOR.
# --------------------------------------------------------------------------
class _FakeStore:
    def __init__(self):
        self.snaps = 0

    async def add_snapshot(self, *a, **k):
        self.snaps += 1


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._p = payload or {"bids": [["1.0", "10"], ["0.9", "10"], ["0.8", "10"]],
                              "asks": [["1.1", "10"], ["1.2", "10"], ["1.3", "10"]]}

    async def json(self):
        return self._p

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._r = resp

    def get(self, url):
        return self._r


def test_successful_poll_stamps_last_ok():
    store = _FakeStore()
    p = dc.SpotDepthPoller(store, [("mexc", "H_USDT")])
    check("a fresh poller has never succeeded", p.last_ok_at == 0.0)

    asyncio.run(p._one(_FakeSession(_FakeResp()), "mexc", "H_USDT"))
    check("a SUCCESSFUL poll writes a snapshot", store.snaps == 1)
    check("...and stamps last_ok_at", p.last_ok_at > 0.0, f"{p.last_ok_at:.3f}")

    stamped = p.last_ok_at
    asyncio.run(p._one(_FakeSession(_FakeResp(status=503)), "mexc", "H_USDT"))
    check("a FAILED poll does NOT advance the stamp", p.last_ok_at == stamped)
    check("...and does not write a snapshot", store.snaps == 1)
    check("...and is counted as an error", p.errors == 1)


def test_stamp_feeds_the_decision():
    """End to end: a stamped poller that then goes quiet must escalate."""
    store = _FakeStore()
    p = dc.SpotDepthPoller(store, [("mexc", "H_USDT")])
    asyncio.run(p._one(_FakeSession(_FakeResp()), "mexc", "H_USDT"))
    import time
    age_now = time.monotonic() - p.last_ok_at
    check("a just-polled sweep is healthy",
          evaluate_stall([60.0], age_now)[0] == "ok", f"age {age_now:.2f}s")
    # the same stamp, 50 minutes later
    check("the same poller silent for 50min HARD stalls",
          evaluate_stall([60.0], age_now + 50 * MIN)[0] == "hard")


def main() -> int:
    print("depth stall escalation — spot and perp, on their own clocks")
    for t in (test_measured_reality_does_not_thrash,
              test_the_old_perp_only_rule_MISSED_the_real_stall,
              test_each_side_escalates_on_its_own_break,
              test_never_seen_is_not_healthy_for_perp_but_is_skipped_for_spot,
              test_successful_poll_stamps_last_ok,
              test_stamp_feeds_the_decision):
        print(f"\n{t.__name__}")
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
