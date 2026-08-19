#!/usr/bin/env python3
"""Exercises the carry bot's funding-accrual path against REAL data.

WHY THIS EXISTS: a freshly opened paper position cannot accrue until the next
funding boundary, which is up to 4 hours away. That would leave the accrual
code unverified at hand-over time. This test opens a position whose
`last_epoch` is the PREVIOUS real epoch, so the very next accrue() call must
settle exactly one epoch using the real funding_rate observed at that real
boundary — the same code path the service runs, no mocks.

It uses its own run_id and DELETES its rows afterwards, so the production paper
track record stays clean. Places no orders; reads only.
"""
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/vadym/mexc-trade-bot/researcher")

import asyncpg                                            # noqa: E402
from dotenv import load_dotenv                            # noqa: E402

load_dotenv(Path("/home/vadym/mexc-trade-bot/researcher/.env"))

from app.carry.bot.config import CarryBotConfig           # noqa: E402
from app.carry.bot.intervals import IntervalResolver      # noqa: E402
from app.carry.bot.main import CarryBot                   # noqa: E402

RUN_ID = "accrual-test"


async def main() -> int:
    cfg = CarryBotConfig()
    pool = await asyncpg.create_pool(dsn=os.environ["NEON_DATABASE_URL"],
                                     min_size=1, max_size=2,
                                     statement_cache_size=0)
    bot = CarryBot(cfg, pool, RUN_ID)
    await bot.setup()

    # a name with a real, resolvable interval and recent funding data
    row = await pool.fetchrow(
        """SELECT exchange, symbol FROM paper_carry_positions
           WHERE status='open' AND run_id<>$1 AND leg='perp'
           ORDER BY id DESC LIMIT 1""", RUN_ID)
    if row is None:
        print("no live paper position to copy a symbol from")
        return 1
    ex, sym = row["exchange"], row["symbol"]
    iv = await bot.intervals.get(ex, sym)
    now = dt.datetime.now(dt.timezone.utc)
    now_epoch = IntervalResolver.epoch_index(now, iv)
    prev_epoch = now_epoch - 1
    boundary = IntervalResolver.epoch_start(now_epoch, iv)

    print("=" * 78)
    print(f"ACCRUAL TEST — {ex}/{sym}, real interval {iv:.0f}h")
    print(f"  now              {now:%Y-%m-%d %H:%M:%S} UTC")
    print(f"  current epoch    {now_epoch}  (started {boundary:%Y-%m-%d %H:%M} UTC)")
    print(f"  seeding position at epoch {prev_epoch} so exactly ONE epoch is owed")
    print("=" * 78)

    notional = 1000.0
    gid = f"{RUN_ID}-{ex}-{sym}"
    for leg, side, price in (("spot", "long", 1.0), ("perp", "short", 1.0)):
        await bot.store.open_leg(gid, ex, sym, leg, side, notional, price,
                                 cfg.leverage if leg == "perp" else 1.0,
                                 0.0, iv, prev_epoch, 1e9, "test", "accrual test")

    before = await pool.fetchrow(
        """SELECT realised_funding_usd, modelled_funding_usd, paper_pnl_usd, last_epoch
           FROM paper_carry_positions WHERE group_id=$1 AND leg='perp'""", gid)
    print(f"\nBEFORE: realised=${before['realised_funding_usd']:.6f} "
          f"modelled=${before['modelled_funding_usd']:.6f} "
          f"pnl=${before['paper_pnl_usd']:.6f} last_epoch={before['last_epoch']}")

    n = await bot.accrue()

    after = await pool.fetchrow(
        """SELECT realised_funding_usd, modelled_funding_usd, paper_pnl_usd, last_epoch
           FROM paper_carry_positions WHERE group_id=$1 AND leg='perp'""", gid)
    print(f"AFTER : realised=${after['realised_funding_usd']:.6f} "
          f"modelled=${after['modelled_funding_usd']:.6f} "
          f"pnl=${after['paper_pnl_usd']:.6f} last_epoch={after['last_epoch']}")
    print(f"\naccrue() settled {n} epoch(s)")

    ev = await pool.fetch(
        """SELECT ts, message, data FROM paper_carry_events
           WHERE run_id=$1 AND kind='accrue' ORDER BY id DESC LIMIT 3""", RUN_ID)
    for e in ev:
        print(f"  EVENT {e['ts']:%H:%M:%S} {e['message']}")

    moved = float(after["realised_funding_usd"]) != float(before["realised_funding_usd"])
    epoch_ok = int(after["last_epoch"]) == now_epoch
    pnl_ok = abs(float(after["paper_pnl_usd"]) - float(after["realised_funding_usd"])) < 1e-9
    ok = n >= 1 and moved and epoch_ok and pnl_ok

    print(f"\n  accrued >=1 epoch          : {'PASS' if n >= 1 else 'FAIL'}")
    print(f"  realised funding changed   : {'PASS' if moved else 'FAIL'}")
    print(f"  last_epoch advanced to now : {'PASS' if epoch_ok else 'FAIL'}")
    print(f"  pnl tracks realised funding: {'PASS' if pnl_ok else 'FAIL'}")

    # cleanup — keep the production paper record clean
    await pool.execute("DELETE FROM paper_carry_positions WHERE run_id=$1", RUN_ID)
    await pool.execute("DELETE FROM paper_carry_events WHERE run_id=$1", RUN_ID)
    print(f"\n  cleaned up run_id={RUN_ID}")
    print("=" * 78)
    print(f"ACCRUAL TEST: {'PASS' if ok else 'FAIL'}")
    await pool.close()
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
