"""Liveness measured from the STRATEGY'S OWN ARTEFACTS, not from a heartbeat.

WHAT THIS EXISTS FOR — the 2026-09-14 post-mortem
-------------------------------------------------
Between 2026-09-05 and 2026-09-14 the bot logged 2,554 `cycle failed` lines and
stopped accruing and selecting for **55+ hours in total** (the longest single
episode 28.07 h, 09-10 12:00Z -> 09-11 16:04Z). systemd reported the unit
`active (running)` the entire time. Nothing alerted.

The mechanism was NOT a stalled thread — the loop is single-threaded. It was an
exception thrown PART-WAY THROUGH the cycle:

    cycle():  health()  ->  check_risk()  ->  accrue()  ->  rebalance()  -> report()
                 |              |                |
                 +-- writes risk events ---------+            X  never reached

`check_risk` raised on every tick (an untyped SQL parameter in `close_group`),
so the cycle aborted after the risk events had been written and before the
accrual was. The observable signature is therefore:

    risk events    -> continuous, max gap 14.9 min, looks perfectly healthy
    accrual/select -> silent for over a day

**The defect class is not "a probe on the wrong thread". It is a liveness
signal emitted BEFORE the point of failure.** Anything that reports "I am
alive" early in a body of work will keep reporting it while the rest of that
work is failing. The only signal that cannot lie this way is one derived from
the work's OUTPUT — a receipt actually written, a selection pass actually
completed — which is what this module measures.

WHY THE THRESHOLD IS TIED TO THE FUNDING INTERVAL
-------------------------------------------------
An accrual is not due on a fixed clock: it is due when a settlement epoch
passes. The shortest settlement interval in the OPEN book is therefore the
longest we can go without a receipt and still be sure nothing was missed. Going
past it by `accrual_grace_mult` means at least one epoch has certainly been
missed — which is loud by construction rather than by a number someone picked.

An empty book accrues nothing, so the accrual check is skipped when there are
no open positions; that is an absence of work, not a failure to do it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OK, WARN, STALLED = "ok", "warn", "stalled"


class LivenessVerdict:
    """A verdict with the inputs that produced it sitting beside it."""

    __slots__ = ("severity", "reasons", "data")

    def __init__(self, severity: str, reasons: list[str], data: dict) -> None:
        self.severity = severity
        self.reasons = reasons
        self.data = data

    @property
    def fired(self) -> bool:
        return self.severity != OK

    @property
    def detail(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "strategy live"

    def __repr__(self) -> str:
        return f"<LivenessVerdict {self.severity}: {self.detail}>"


def evaluate(*, open_positions: int,
             min_interval_h: float | None,
             accrual_age_s: float | None,
             select_age_s: float | None,
             select_every_min: float,
             consecutive_failures: int,
             accrual_grace_mult: float = 1.25,
             select_stall_mult: float = 3.0,
             fail_escalate: int = 3) -> LivenessVerdict:
    """Pure decision. No clock, no database — every input is passed in, so the
    test can put the strategy into a stall without waiting for one.

    `accrual_age_s` / `select_age_s` are seconds since the last receipt was
    WRITTEN and the last selection pass COMPLETED. `None` means "never seen in
    this process and nothing in the database either" — treated as unknown, not
    as healthy, and reported as such once the process has had a chance to run.
    """
    reasons: list[str] = []
    severity = OK
    data: dict = {"open_positions": open_positions,
                  "min_interval_h": min_interval_h,
                  "accrual_age_s": accrual_age_s,
                  "select_age_s": select_age_s,
                  "consecutive_failures": consecutive_failures}

    def escalate(level: str) -> None:
        nonlocal severity
        if level == STALLED or severity == STALLED:
            severity = STALLED
        elif level == WARN and severity == OK:
            severity = WARN

    # ---- accrual: the receipt the strategy exists to produce ---------------
    if open_positions > 0 and min_interval_h:
        limit = min_interval_h * 3600.0 * accrual_grace_mult
        data["accrual_limit_s"] = limit
        if accrual_age_s is None:
            reasons.append(
                f"no funding receipt has EVER been written while {open_positions} "
                f"position(s) are open")
            escalate(STALLED)
        elif accrual_age_s > limit:
            missed = accrual_age_s / (min_interval_h * 3600.0)
            reasons.append(
                f"no funding receipt for {accrual_age_s / 3600.0:.1f}h "
                f"(limit {limit / 3600.0:.1f}h = {accrual_grace_mult:g}x the "
                f"{min_interval_h:g}h settlement interval) — "
                f"~{missed:.1f} epoch(s) missed on {open_positions} open position(s)")
            escalate(STALLED)

    # ---- selection: the other half of the strategy actually running --------
    select_limit = select_every_min * 60.0 * select_stall_mult
    data["select_limit_s"] = select_limit
    if select_age_s is None:
        reasons.append("no selection pass has EVER completed")
        escalate(WARN)
    elif select_age_s > select_limit:
        reasons.append(
            f"no selection pass completed for {select_age_s / 60.0:.0f}min "
            f"(limit {select_limit / 60.0:.0f}min = {select_stall_mult:g}x the "
            f"{select_every_min:g}min cadence)")
        escalate(STALLED)

    # ---- the same exception, over and over ---------------------------------
    # A cycle that fails once is noise. A cycle that fails N times in a row is
    # a strategy that has stopped, and the 2026-09 episode failed ~1,700 times
    # in a row at INFO-adjacent volume without anything changing level.
    if consecutive_failures >= fail_escalate:
        reasons.append(f"{consecutive_failures} consecutive cycle failures")
        escalate(STALLED)

    return LivenessVerdict(severity, reasons, data)


SEED_SQL = """
SELECT (SELECT max(ts) FROM paper_carry_events WHERE kind='accrue' AND run_id=$1) AS last_accrual,
       (SELECT max(ts) FROM paper_carry_events WHERE kind='select' AND run_id=$1) AS last_select,
       (SELECT count(*)        FROM paper_carry_positions WHERE status='open' AND run_id=$1) AS open_legs,
       (SELECT min(interval_hours) FROM paper_carry_positions WHERE status='open' AND run_id=$1) AS min_interval_h
"""


class LivenessMarks:
    """Monotonic marks stamped BY THE WORK, seeded from the database at start.

    In-process and monotonic so a database hiccup cannot make the strategy look
    alive; seeded from the database so a restart does not reset a real stall to
    zero and hide it.
    """

    __slots__ = ("_accrual", "_select", "_clock", "consecutive_failures")

    def __init__(self, clock) -> None:
        self._clock = clock            # callable -> monotonic seconds
        self._accrual: float | None = None
        self._select: float | None = None
        self.consecutive_failures = 0

    def seed(self, accrual_age_s: float | None, select_age_s: float | None) -> None:
        now = self._clock()
        if accrual_age_s is not None:
            self._accrual = now - accrual_age_s
        if select_age_s is not None:
            self._select = now - select_age_s

    def accrual_written(self) -> None:
        self._accrual = self._clock()

    def select_completed(self) -> None:
        self._select = self._clock()

    def cycle_ok(self) -> None:
        self.consecutive_failures = 0

    def cycle_failed(self) -> None:
        self.consecutive_failures += 1

    @property
    def accrual_age_s(self) -> float | None:
        return None if self._accrual is None else self._clock() - self._accrual

    @property
    def select_age_s(self) -> float | None:
        return None if self._select is None else self._clock() - self._select
