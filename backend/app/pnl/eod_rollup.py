# app/pnl/eod_rollup.py
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.pnl_ledger import PnlLedger
from . import repository as repo


def _utc_day_bounds(day: date) -> Tuple[datetime, datetime]:
    """
    Inclusive-exclusive UTC bounds for a given date: [00:00, next day 00:00)
    Returned as naive datetimes (SQLite-friendly).
    """
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    end = (datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
    return start, end


def rollup_day(db: Session, day: date, scope: Optional[repo.Scope] = None) -> int:
    """
    Aggregate pnl_ledger into pnl_daily for a single UTC day.

    For each (exchange, account_id, symbol) on this day:
      - realized_usd = sum(amount_usd WHERE event_type=TRADE_REALIZED OR CONVERSION_PNL OR FUNDING)
      - fees_usd     = sum(amount_usd WHERE event_type=FEE)

    Returns number of upserted rows.
    """
    start_naive, end_naive = _utc_day_bounds(day)

    q = (
        select(
            PnlLedger.exchange,
            PnlLedger.account_id,
            PnlLedger.symbol,
            PnlLedger.event_type,
            func.coalesce(func.sum(PnlLedger.amount_usd), 0),
        )
        .where(PnlLedger.ts >= start_naive)
        .where(PnlLedger.ts < end_naive)
        .group_by(PnlLedger.exchange, PnlLedger.account_id, PnlLedger.symbol, PnlLedger.event_type)
    )

    # Apply scope if provided
    if scope:
        if scope.get("exchange"):
            q = q.where(PnlLedger.exchange == scope["exchange"])
        if scope.get("account_id"):
            q = q.where(PnlLedger.account_id == scope["account_id"])
        if scope.get("symbol"):
            q = q.where(PnlLedger.symbol == scope["symbol"])

    rows = db.execute(q).all()

    # Accumulate per (ex, acc, sym)
    acc: Dict[Tuple[str, str, str], Dict[str, Decimal]] = {}
    for ex, acc_id, sym, etype, amt in rows:
        key = (ex, acc_id, sym)
        bucket = acc.setdefault(
            key,
            {"realized": Decimal("0"), "fees": Decimal("0")},
        )
        amt_dec = Decimal(amt or 0)

        if etype == "FEE":
            bucket["fees"] += amt_dec
        else:
            # TRADE_REALIZED, FUNDING, CONVERSION_PNL all affect realized bucket
            bucket["realized"] += amt_dec

    upserts = 0
    for (ex, acc_id, sym), parts in acc.items():
        repo.upsert_daily_row(
            db,
            day=day,
            exchange=ex,
            account_id=acc_id,
            symbol=sym,
            realized_usd=parts["realized"],
            fees_usd=parts["fees"],
        )
        upserts += 1

    return upserts


def rollup_range(db: Session, start_day: date, end_day: date, scope: Optional[repo.Scope] = None) -> int:
    """
    Aggregate inclusive range of days [start_day, end_day].
    Returns total number of upserted rows across the range.
    """
    if end_day < start_day:
        return 0

    total = 0
    cur = start_day
    while cur <= end_day:
        total += rollup_day(db, cur, scope=scope)
        cur = cur + timedelta(days=1)
    return total
