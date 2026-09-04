"""ONE-SHOT (2026-09-04): apply the basis-leg schema and BACKFILL existing positions.

    usage:  researcher/.venv/bin/python scripts/backfill_basis_leg.py [--apply]

Kept in the tree because the backfilled marks are part of the P&L record and
the code that produced them has to be auditable. Backfilled rows are flagged
`backfill-median2h` in `basis_mark_source` and are never confusable with
live-recorded ones. The estimator is IDENTICAL to the live one — `basis.mark` —
so the two differ only in when they were computed.

Ran against 46 positions; 41 of 41 closed positions fully marked; booked basis
+$0.7459 against a funding-only −$2.2711. Idempotent: re-running recomputes the
same marks from the same snapshots. Default is a DRY RUN.
"""
import asyncio, os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
import asyncpg
from app.carry.bot import basis
from app.carry.bot.store import _SCHEMA
from app.carry.bot.config import CarryBotConfig

DRY = "--apply" not in sys.argv

async def main():
    cfg = CarryBotConfig()
    pool = await asyncpg.create_pool(dsn=os.environ["NEON_DATABASE_URL"],
                                     min_size=1, max_size=4)
    async with pool.acquire() as c:
        before = await c.fetchrow(
            """SELECT count(*) legs, count(DISTINCT group_id) grps,
                      sum(paper_pnl_usd) pnl FROM paper_carry_positions""")
        print(f"BEFORE: {before['legs']} legs, {before['grps']} groups, "
              f"paper_pnl=${float(before['pnl']):+.4f}")
        print("applying schema ...")
        await c.execute(_SCHEMA)
        print("schema applied (columns added, funding_only seeded from paper_pnl)")

        rows = await c.fetch(
            """SELECT group_id, exchange, symbol, status,
                      min(opened_ts) AS opened_ts, max(closed_ts) AS closed_ts,
                      max(notional_usd) FILTER (WHERE leg='spot') AS notional
               FROM paper_carry_positions
               GROUP BY group_id, exchange, symbol, status
               ORDER BY min(opened_ts)""")
        print(f"\nbackfilling {len(rows)} positions "
              f"(window {cfg.basis_mark_window_h:g}h trailing median mid basis)\n")
        print(f"{'position':<28} {'st':<7} {'notl':>7} {'entry':>9} {'exit':>9} "
              f"{'n_in':>5} {'n_out':>5} {'basis$':>9}")
        tot_basis = 0.0; marked = 0; unmarked = []
        for r in rows:
            ex, sym = r["exchange"], r["symbol"]
            eb = await basis.mark(c, ex, sym, at=r["opened_ts"],
                                  window_h=cfg.basis_mark_window_h,
                                  source="backfill")
            xb = (await basis.mark(c, ex, sym, at=r["closed_ts"],
                                   window_h=cfg.basis_mark_window_h,
                                   source="backfill")
                  if r["status"] == "closed" and r["closed_ts"] else None)
            n = float(r["notional"] or 0.0)
            bp = basis.basis_pnl_usd(n, eb.bps if eb.ok else None,
                                     xb.bps if (xb and xb.ok) else None)
            ok = eb.ok and (xb is not None and xb.ok)
            if r["status"] == "closed":
                if ok: marked += 1
                else: unmarked.append(f"{ex}/{sym}")
            tot_basis += bp
            print(f"{ex+'/'+sym:<28} {r['status']:<7} {n:>7.1f} "
                  f"{(f'{eb.bps:+.1f}' if eb.ok else '  --'):>9} "
                  f"{(f'{xb.bps:+.1f}' if (xb and xb.ok) else '  --'):>9} "
                  f"{eb.n:>5} {(xb.n if xb else 0):>5} {bp:>+9.4f}")
            if DRY:
                continue
            await c.execute(
                """UPDATE paper_carry_positions
                   SET entry_basis_bps = $2, entry_basis_ts = $3, entry_basis_n = $4,
                       exit_basis_bps  = $5, exit_basis_ts  = $6, exit_basis_n  = $7,
                       basis_mark_source = $8,
                       basis_pnl_usd = CASE WHEN leg='spot'
                                            THEN $9::double precision ELSE 0 END,
                       paper_pnl_usd = funding_only_pnl_usd
                                       + CASE WHEN leg='spot'
                                              THEN $9::double precision ELSE 0 END
                   WHERE group_id=$1""",
                r["group_id"],
                eb.bps if eb.ok else None, eb.last_ts if eb.ok else None,
                eb.n if eb.ok else None,
                xb.bps if (xb and xb.ok) else None,
                xb.last_ts if (xb and xb.ok) else None,
                xb.n if (xb and xb.ok) else None,
                f"{eb.source} -> {xb.source if xb else 'open'}", bp)

        print(f"\ntotal basis P&L over all positions: ${tot_basis:+.4f}")
        print(f"closed positions fully marked: {marked}"
              + (f"; UNMARKED: {', '.join(unmarked)}" if unmarked else ""))
        if DRY:
            print("\nDRY RUN — nothing written. re-run with --apply")
        else:
            after = await c.fetchrow(
                """SELECT sum(paper_pnl_usd) pnl, sum(funding_only_pnl_usd) fo,
                          sum(basis_pnl_usd) b FROM paper_carry_positions""")
            print(f"\nAFTER: paper_pnl=${float(after['pnl']):+.4f} "
                  f"= funding_only ${float(after['fo']):+.4f} "
                  f"+ basis ${float(after['b']):+.4f}")
    await pool.close()

asyncio.run(main())
