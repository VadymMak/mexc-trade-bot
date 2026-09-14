#!/usr/bin/env python3
"""The curve-age stamp — driven through the REAL path, against the live books.

    usage:  researcher/.venv/bin/python tests/test_book_age.py

WHY THIS EXISTS. `book.latest_curve` takes `max(ts)` with NO age bound, so a
dead depth feed does not yield "no price" — it yields an arbitrarily old price
with nothing marking it as old. A missing price fails loudly; a stale one fails
silently, which is the worse of the two and is the same shape as the basis marks
before 2026-09-04. Measured 2026-09-14 over 24 h: perp books were never worse
than 3.9 min stale, spot reached 22.5 min, so an exit CAN be priced off a
twenty-minute-old book today and nothing in the record would say so.

The remedy is the one that already worked for the basis mark: INPUTS BESIDE
OUTPUTS. `entry_book_age_s` / `exit_book_age_s` sit next to the cost columns.

NO LIMIT IS ENFORCED, deliberately. A threshold picked before the distribution
is known is exactly how the depth watchdog came to guard the leg that does not
fail. Measure first.

AND IT DRIVES THE PATH. Generations 21-24 died because a smoke test checked that
an object could be CONSTRUCTED rather than that the new code could RUN. Every
check below calls the real function against the real database.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def dsn():
    d = (os.getenv("CARRY_DSN") or os.getenv("NEON_DATABASE_URL")
         or os.getenv("DATABASE_URL"))
    if d:
        return d
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env):
        m = re.search(r"^NEON_DATABASE_URL=(.*)$", open(env).read(), re.M)
        return m.group(1).strip() if m else None
    return None


async def run():
    try:
        import asyncpg
    except ImportError:
        print("  SKIP  (asyncpg unavailable)")
        return
    d = dsn()
    if not d:
        print("  SKIP  (no DSN)")
        return
    os.environ.setdefault("CARRY_BOT_MODE", "paper")
    from app.carry.bot.config import CarryBotConfig
    from app.carry.bot.book import BookSource
    from app.carry.bot.executor import build_executor

    pool = await asyncpg.create_pool(d, min_size=1, max_size=3,
                                     statement_cache_size=0)
    try:
        cfg = CarryBotConfig()
        books = BookSource(pool, cfg)

        # --- the schema actually carries the columns, with the right type ---
        cols = {r["column_name"]: r["data_type"] for r in await pool.fetch(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_name='paper_carry_positions'
                 AND column_name IN ('entry_book_age_s','exit_book_age_s')""")}
        check("entry_book_age_s exists as double precision",
              cols.get("entry_book_age_s") == "double precision", str(cols))
        check("exit_book_age_s exists as double precision",
              cols.get("exit_book_age_s") == "double precision", str(cols))

        # --- pick a name that really has books right now -------------------
        row = await pool.fetchrow(
            """SELECT exchange, symbol FROM carry_book_l2
               WHERE ts > now() - interval '30 minutes'
               GROUP BY 1,2 HAVING count(*) > 100 LIMIT 1""")
        if not row:
            print("  SKIP  (no recent books)")
            return
        ex, sym = row["exchange"], row["symbol"]
        print(f"    driving the real path on {ex}/{sym}")

        # --- latest_curve stamps a REAL, non-trivial age -------------------
        c = await books.latest_curve(ex, sym, "perp", "bid")
        check("latest_curve returns a curve", c is not None)
        if c is None:
            return
        check("...carrying the snapshot ts", c.ts is not None, str(c.ts))
        check("...carrying a numeric age", isinstance(c.age_s, float), str(c.age_s))
        check("...the age is non-negative", c.age_s >= 0.0, f"{c.age_s:.1f}s")
        check("...and NON-TRIVIAL (a real book is never 0.0s old)",
              c.age_s > 0.0, f"{c.age_s:.1f}s")
        check("...and plausible for a 2-min collector (< 2 h)",
              c.age_s < 7200, f"{c.age_s:.1f}s")

        # the age must track the snapshot, not the wall clock
        db_age = await pool.fetchval(
            """SELECT extract(epoch FROM now()-max(ts)) FROM carry_book_l2
               WHERE exchange=$1 AND symbol=$2 AND market='perp'""", ex, sym)
        check("...and agrees with the database's own view of staleness",
              abs(c.age_s - float(db_age)) < 5.0,
              f"curve {c.age_s:.1f}s vs db {float(db_age):.1f}s")

        # --- the age PROPAGATES through the executor, both directions ------
        ex_ = build_executor(books, cfg)
        res = await ex_.open_carry(ex, sym, 50.0)
        if res.ok:
            check("open_carry propagates book_age_s",
                  res.book_age_s is not None and res.book_age_s > 0,
                  f"{res.book_age_s:.1f}s")
            check("...and it is the WORSE of the two legs",
                  res.book_age_s >= c.age_s - 5.0,
                  f"entry {res.book_age_s:.1f}s vs perp-bid {c.age_s:.1f}s")
        else:
            print(f"    (open_carry refused: {res.reason} — age check skipped)")

        out = await ex_.close_carry(ex, sym, 50.0)
        check("close_carry returns 5 elements incl. the age", len(out) == 5,
              f"len={len(out)}")
        check("...and the exit age is populated",
              out[4] is not None and out[4] > 0,
              f"{out[4]:.1f}s" if out[4] is not None else "None")

        # --- THE STORAGE PATH, for real, inside a ROLLED-BACK transaction ---
        # The columns exist and the executor computes the age; neither proves
        # the value SURVIVES the write. This drives the real `open_leg` and
        # `close_group` SQL and reads the row back, then rolls the whole thing
        # away so the book of record is untouched.
        from app.carry.bot.store import BotStore
        from app.carry.bot import basis as basis_mod

        class _Rollback(Exception):
            pass

        stored = {}
        async with pool.acquire() as conn:
            try:
                async with conn.transaction():
                    st = BotStore(conn, "TEST-ROLLBACK-book-age")
                    gid = "TEST-ROLLBACK-grp"
                    eb = await basis_mod.mark(conn, ex, sym, window_h=2.0)
                    for leg, side, px in (("spot", "long", 1.0), ("perp", "short", 1.0)):
                        await st.open_leg(gid, ex, sym, leg, side, 50.0, px, 1.0,
                                          0.01, 4.0, 1, 100.0, "worst-hour",
                                          "rollback test", entry_basis=eb,
                                          book_age_s=res.book_age_s if res.ok else 123.4)
                    r = await conn.fetchrow(
                        "SELECT leg, entry_book_age_s FROM paper_carry_positions "
                        "WHERE group_id=$1 ORDER BY leg", gid)
                    stored["entry"] = r["entry_book_age_s"]
                    await st.close_group(gid, 0.02, "rollback test",
                                         exit_basis=eb, spot_close_price=None,
                                         perp_close_price=None, book_age_s=out[4])
                    r2 = await conn.fetchrow(
                        "SELECT exit_book_age_s, status FROM paper_carry_positions "
                        "WHERE group_id=$1 LIMIT 1", gid)
                    stored["exit"] = r2["exit_book_age_s"]
                    stored["status"] = r2["status"]
                    raise _Rollback
            except _Rollback:
                pass
        check("open_leg STORES a non-trivial entry_book_age_s",
              stored.get("entry") is not None and stored["entry"] > 0,
              f"{stored.get('entry')}")
        check("close_group STORES a non-trivial exit_book_age_s",
              stored.get("exit") is not None and stored["exit"] > 0,
              f"{stored.get('exit')}")
        check("...on a row that really did close", stored.get("status") == "closed",
              str(stored.get("status")))
        left = await pool.fetchval(
            "SELECT count(*) FROM paper_carry_positions WHERE group_id=$1",
            "TEST-ROLLBACK-grp")
        check("the transaction rolled back — book of record untouched", left == 0,
              f"{left} rows left behind")
    finally:
        await pool.close()


def main() -> int:
    print("curve age stamp — inputs beside outputs, driven through the real path")
    asyncio.run(run())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
