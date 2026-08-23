"""Carry bot runner — PAPER MODE.

    cd /home/vadym/mexc-trade-bot/researcher && .venv/bin/python -m app.carry.bot.main
    ... --once      one cycle, print a report, exit (the smoke run)

PLACES NO ORDERS. Holds no credentials. The only network calls in this package
are IntervalResolver's two PUBLIC read-only endpoints; everything else reads
Postgres tables the collectors already fill.

Loop:
  1. health   — kill switch, data staleness (R8/R9). Stale data => act on nothing.
  2. risk     — every open position against R2/R4/R5 + neutrality; exit on fire.
  3. accrue   — any funding epoch crossed since last tick, at the REAL interval.
  4. select   — re-rank the universe, open into unused capital.
  5. report   — realised vs modelled funding, paper net APR vs the model.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import logging
import os
import signal
import sys
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from .book import BookSource
from .config import CarryBotConfig
from .executor import build_executor
from .intervals import IntervalResolver
from .risk import NeutralityManager, RiskManager
from .selector import Selector
from .store import BotStore

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("carry-bot")

MODEL_APR_LO, MODEL_APR_HI = 31.0, 33.0        # the claim under test


class CarryBot:
    def __init__(self, cfg: CarryBotConfig, pool, run_id: str) -> None:
        self.cfg = cfg
        self.pool = pool
        self.store = BotStore(pool, run_id)
        self.books = BookSource(pool, cfg)
        self.intervals = IntervalResolver(pool)
        self.selector = Selector(pool, cfg, self.intervals, self.books, self.store)
        self.risk = RiskManager(cfg, self.books, self.store)
        self.neutral = NeutralityManager(cfg)
        self.exec = build_executor(self.books, cfg)
        self.halted = False

    async def setup(self) -> None:
        await self.store.ensure_schema()
        await self.intervals.ensure_schema()
        assert not getattr(self.exec, "places_real_orders", False), \
            "refusing to run: executor claims it places real orders"
        await self.store.event(
            "info", "health",
            f"carry bot start — {self.cfg.describe()} | executor={self.exec.mode} "
            f"places_real_orders={self.exec.places_real_orders}")

    # ---- 1. health --------------------------------------------------------
    async def health(self) -> bool:
        v = self.risk.kill_switch()
        if v.fired:
            await self.store.event("risk", "risk", f"{v.rule}: {v.detail}")
            return False
        row = await self.pool.fetchrow(
            """SELECT extract(epoch FROM (now() - max(ts)))/60.0 AS age
               FROM funding_basis_snapshots""")
        row2 = await self.pool.fetchrow(
            """SELECT extract(epoch FROM (now() - max(ts)))/60.0 AS age
               FROM carry_book_l2""")
        v = self.risk.data_staleness(float(row["age"] or 1e9),
                                     float(row2["age"] or 1e9))
        if v.fired:
            await self.store.event("risk", "risk", f"{v.rule}: {v.detail}", data=v.data)
            return False
        logger.info("[carry/bot][health] %s | %s", v.detail,
                    "kill switch absent")
        return True

    # ---- 2. risk ----------------------------------------------------------
    async def check_risk(self) -> None:
        groups = await self.store.open_groups()
        for g in groups:
            ex, sym = g["exchange"], g["symbol"]
            quote = await self.pool.fetchrow(
                """SELECT perp_mark, spot_price FROM funding_basis_snapshots
                   WHERE exchange=$1 AND symbol=$2 ORDER BY ts DESC LIMIT 1""",
                ex, sym)
            verdicts = []
            verdicts.append(await self.risk.funding_flip(
                ex, sym, float(g["interval_hours"] or 8.0)))
            verdicts.append(await self.risk.depth_collapse(
                ex, sym, float(g["notional_usd"]), float(g["entry_depth_usd"] or 0.0)))
            if quote:
                verdicts.append(self.risk.margin(
                    float(g["perp_entry"] or 0.0), float(quote["perp_mark"] or 0.0)))
                verdicts.append(self.neutral.check(
                    float(g["notional_usd"]), float(g["spot_entry"] or 0.0),
                    float(g["perp_entry"] or 0.0), float(quote["spot_price"] or 0.0),
                    float(quote["perp_mark"] or 0.0)))
            # log EVERY rule, fired or not — quiet must be visibly tested
            for v in verdicts:
                await self.store.event(
                    "risk" if v.fired else "info", "risk",
                    f"{v.rule} {'FIRED->' + v.action if v.fired else 'ok'}: {v.detail}",
                    ex, sym, v.data)
            exits = [v for v in verdicts if v.fired and v.action == "exit"]
            if exits:
                cost, note = await self.exec.close_carry(
                    ex, sym, float(g["notional_usd"]))
                reason = "; ".join(v.rule for v in exits)
                await self.store.close_group(g["group_id"], cost, reason)
                await self.store.event(
                    "risk", "close",
                    f"closed on {reason} — exit cost ${cost:,.2f} ({note})",
                    ex, sym, {"exit_cost_usd": cost})
                # Exile it. Without this the selector re-admitted the name on
                # the next pass (~65 min) and R4 exited it again ~62s later:
                # 23 TUT round-trips in 48h for $8.26 of cost and $0.11 of
                # funding. The exit and the re-entry gate now agree.
                hours, exits_n = await self.store.block_reentry(
                    ex, sym, self.cfg.reentry_cooldown_hours, reason,
                    self.cfg.max_cooldown_stacks)
                await self.store.event(
                    "risk", "close",
                    f"re-entry BLOCKED for {hours:.0f}h (exit #{exits_n}); "
                    f"after that it must hold trailing-7 AND trailing-3 "
                    f">= {self.cfg.reentry_apr:.0f}% on capital with no negative "
                    f"epochs (exit floor is {self.cfg.min_hold_apr:.0f}%)",
                    ex, sym, {"cooldown_hours": hours, "exits": exits_n})

    # ---- 3. accrue --------------------------------------------------------
    async def accrue(self) -> int:
        """Accrue every funding epoch crossed since the position's last one,
        using the REAL interval and the rate observed nearest that boundary."""
        n = 0
        for g in await self.store.open_groups():
            ex, sym = g["exchange"], g["symbol"]
            iv = float(g["interval_hours"] or 8.0)
            now_epoch = IntervalResolver.epoch_index(
                dt.datetime.now(dt.timezone.utc), iv)
            last = int(g["last_epoch"] or now_epoch)
            for epoch in range(last + 1, now_epoch + 1):
                boundary = IntervalResolver.epoch_start(epoch, iv)
                row = await self.pool.fetchrow(
                    """SELECT funding_rate FROM funding_basis_snapshots
                       WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
                             AND ts <= $3
                       ORDER BY ts DESC LIMIT 1""", ex, sym, boundary)
                if row is None:
                    continue
                rate = float(row["funding_rate"])
                notional = float(g["notional_usd"])
                # short perp RECEIVES when funding is positive
                realised = notional * rate
                modelled = notional * await self._modelled_rate(ex, sym, iv)
                await self.store.accrue(g["group_id"], realised, modelled, epoch)
                await self.store.event(
                    "info", "accrue",
                    f"epoch {epoch} @ {boundary:%Y-%m-%d %H:%M} UTC ({iv:.0f}h): "
                    f"rate {rate:+.6f} -> realised ${realised:+.4f} "
                    f"(modelled ${modelled:+.4f})",
                    ex, sym, {"epoch": epoch, "rate": rate,
                              "realised_usd": realised, "modelled_usd": modelled})
                n += 1
        return n

    async def _modelled_rate(self, ex: str, sym: str, iv: float) -> float:
        row = await self.pool.fetchrow(
            """WITH ep AS (
                 SELECT DISTINCT ON ((floor(extract(epoch FROM ts)/$3))::bigint)
                        funding_rate
                 FROM funding_basis_snapshots
                 WHERE exchange=$1 AND symbol=$2 AND funding_rate IS NOT NULL
                       AND ts > now() - ($4 || ' days')::interval
                 ORDER BY (floor(extract(epoch FROM ts)/$3))::bigint, ts DESC)
               SELECT avg(funding_rate) AS r FROM ep""",
            ex, sym, int(iv * 3600), str(self.cfg.lookback_days))
        return float(row["r"] or 0.0) if row else 0.0

    # ---- 4. select + open -------------------------------------------------
    async def rebalance_basket(self) -> None:
        ranked, allc = await self.selector.select()
        rejects: dict[str, int] = {}
        for c in allc:
            if c.reject:
                rejects[c.reject.split("=")[0]] = rejects.get(c.reject.split("=")[0], 0) + 1
        await self.store.event(
            "info", "select",
            f"{len(ranked)}/{len(allc)} names pass all gates; top: " +
            ", ".join(f"{c.ex}/{c.sym} {c.net_apr:.1f}%" for c in ranked[:5]),
            data={"passed": len(ranked), "evaluated": len(allc),
                  "reject_reasons": rejects})

        open_now = await self.store.open_groups()
        already = {(g["exchange"], g["symbol"]):
                   float(g["notional_usd"]) * self.cfg.capital_multiple
                   for g in open_now}
        used = sum(already.values())
        free = self.cfg.capital_usd - used
        if free <= self.cfg.min_notional_usd * self.cfg.capital_multiple:
            logger.info("[carry/bot][select] capital fully deployed "
                        "($%.0f of $%.0f)", used, self.cfg.capital_usd)
            return

        held = {(g["exchange"], g["symbol"]) for g in open_now}
        fresh = [c for c in ranked if c.key not in held]
        for cand, capital in self.selector.allocate(fresh, free, already):
            notional = capital / self.cfg.capital_multiple
            res = await self.exec.open_carry(cand.ex, cand.sym, notional)
            if not res.ok:
                await self.store.event("warn", "open",
                                       f"open refused: {res.reason}",
                                       cand.ex, cand.sym)
                continue
            iv = cand.iv
            epoch = IntervalResolver.epoch_index(
                dt.datetime.now(dt.timezone.utc), iv)
            note = (f"gross {cand.gross_apr:.1f}% iv {iv:.0f}h "
                    f"net {cand.net_apr:.1f}% slip {cand.slip_bps:.1f}bps "
                    f"trail7 {cand.apr7_cap:.1f}% payback {cand.payback_days:.1f}d "
                    f"depth_basis {cand.depth_basis}")
            for leg, side, price in (("spot", "long", res.spot_price),
                                     ("perp", "short", res.perp_price)):
                await self.store.open_leg(
                    res.group_id, cand.ex, cand.sym, leg, side, notional, price,
                    self.cfg.leverage if leg == "perp" else 1.0,
                    res.entry_cost_usd / 2.0, iv, epoch, cand.depth_usd,
                    cand.depth_basis, note)
            await self.store.event(
                "info", "open",
                f"PAPER open ${notional:,.0f}/leg (capital ${capital:,.0f}) "
                f"spot@{res.spot_price:.6g} perp@{res.perp_price:.6g} "
                f"entry cost ${res.entry_cost_usd:.2f} "
                f"({res.spot_slip:.1f}+{res.perp_slip:.1f}bps slip) | {note}",
                cand.ex, cand.sym,
                {"notional_usd": notional, "capital_usd": capital,
                 "net_apr": cand.net_apr, "gross_apr": cand.gross_apr,
                 "entry_cost_usd": res.entry_cost_usd,
                 "depth_usd": cand.depth_usd, "depth_basis": cand.depth_basis})

    # ---- 5. report --------------------------------------------------------
    async def report(self) -> dict:
        s = await self.store.summary()
        deployed = float(s.get("notional") or 0.0) * self.cfg.capital_multiple
        pnl = float(s.get("pnl") or 0.0)
        first = s.get("first_open")
        days = ((dt.datetime.now(dt.timezone.utc) - first).total_seconds() / 86400.0
                if first else 0.0)
        apr = (pnl / deployed) * (365.0 / days) * 100.0 if deployed and days > 0.02 else float("nan")
        realised, modelled = float(s.get("realised") or 0), float(s.get("modelled") or 0)
        track = (realised / modelled * 100.0) if modelled else float("nan")
        msg = (f"open={s.get('open_groups', 0)} closed={s.get('closed_groups', 0)} "
               f"deployed=${deployed:,.0f} pnl=${pnl:+,.2f} "
               f"funding realised=${realised:+,.4f} vs modelled=${modelled:+,.4f} "
               f"({track:.0f}% of model) | paper APR "
               + (f"{apr:+.1f}%" if apr == apr else "n/a (too early)")
               + f" vs model {MODEL_APR_LO:.0f}-{MODEL_APR_HI:.0f}%")
        await self.store.event("info", "health", msg,
                               data={"deployed_usd": deployed, "pnl_usd": pnl,
                                     "paper_apr": None if apr != apr else apr,
                                     "realised_funding": realised,
                                     "modelled_funding": modelled,
                                     "days": days})
        v = self.risk.drawdown(pnl, deployed)
        if v.fired:
            await self.store.event("risk", "risk", f"{v.rule}: {v.detail}", data=v.data)
            self.halted = True
        return {"apr": apr, "pnl": pnl, "deployed": deployed, "summary": s}

    # ---- cycle ------------------------------------------------------------
    async def cycle(self) -> dict:
        if not await self.health():
            return {"halted": True}
        await self.check_risk()
        n = await self.accrue()
        if n:
            logger.info("[carry/bot][accrue] %d funding epoch(s) accrued", n)
        await self.rebalance_basket()
        return await self.report()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="one cycle then exit (smoke run)")
    args = ap.parse_args()

    cfg = CarryBotConfig()
    if cfg.is_live:
        raise SystemExit("live mode is not implemented — refusing to start")

    dsn = os.getenv("NEON_DATABASE_URL", "")
    if not dsn:
        raise SystemExit("NEON_DATABASE_URL not set")
    run_id = os.getenv("CARRY_RUN_ID", "paper-" + uuid.uuid4().hex[:8])

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4,
                                     command_timeout=120.0, statement_cache_size=0)
    bot = CarryBot(cfg, pool, run_id)
    await bot.setup()
    logger.info("[carry/bot] run_id=%s | %s", run_id, cfg.describe())

    if args.once:
        await bot.cycle()
        await pool.close()
        return

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    last_select = 0.0
    import time as _t
    while not stop.is_set():
        try:
            if _t.monotonic() - last_select > cfg.select_every_min * 60:
                await bot.cycle()
                last_select = _t.monotonic()
            else:
                if await bot.health():
                    await bot.check_risk()
                    if await bot.accrue():
                        await bot.report()
        except Exception as exc:
            logger.exception("[carry/bot] cycle failed: %r", exc)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=cfg.tick_secs)

    logger.info("[carry/bot] shutting down…")
    await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
