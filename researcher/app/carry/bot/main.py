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

from .book import BookSource, live_mid
from .config import CarryBotConfig
from .executor import build_executor
from .intervals import IntervalResolver
from .risk import NeutralityManager, RiskManager, Verdict
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
        # group_id -> consecutive cycles the neutrality threshold has been
        # breached. Reset the moment delta comes back inside the threshold.
        self._neutrality_breaches: dict[str, int] = {}

    async def setup(self) -> None:
        await self.store.ensure_schema()
        await self.intervals.ensure_schema()
        assert not getattr(self.exec, "places_real_orders", False), \
            "refusing to run: executor claims it places real orders"
        await self.store.event(
            "info", "health",
            f"carry bot start — {self.cfg.describe()} | executor={self.exec.mode} "
            f"places_real_orders={self.exec.places_real_orders}")

    # ---- 1. health (PER EXCHANGE) -----------------------------------------
    async def health(self) -> dict:
        """Returns {exchange: action} where action is 'none' | 'freeze' | 'derisk'.

        Returns None only when the whole book must stop (kill switch, or every
        venue dark). Freshness is judged PER VENUE: Gate writing every 5 minutes
        used to keep a table-wide max(ts) fresh while MEXC sat 73 minutes dark
        and we held a MEXC position against it.
        """
        v = self.risk.kill_switch()
        if v.fired:
            await self.store.event("risk", "risk", f"{v.rule}: {v.detail}")
            return None

        rows = await self.pool.fetch(
            """SELECT exchange,
                      extract(epoch FROM (now() - max(ts)))/60.0 AS age
               FROM funding_basis_snapshots
               WHERE ts > now() - interval '2 days'
               GROUP BY exchange""")
        funding_age = {r["exchange"]: float(r["age"] or 1e9) for r in rows}
        rows = await self.pool.fetch(
            """SELECT exchange,
                      extract(epoch FROM (now() - max(ts)))/60.0 AS age
               FROM carry_book_l2
               WHERE ts > now() - interval '2 days'
               GROUP BY exchange""")
        book_age = {r["exchange"]: float(r["age"] or 1e9) for r in rows}

        venues = set(funding_age) | set(book_age) | set(self.cfg.maker_bps)
        actions: dict = {}
        for ex in sorted(venues):
            v = self.risk.data_staleness(ex, funding_age.get(ex, 1e9),
                                         book_age.get(ex, 1e9))
            actions[ex] = v.action
            await self.store.event(
                "risk" if v.fired else "info", "risk",
                f"{v.rule} {'FIRED->' + v.action if v.fired else 'ok'}: {v.detail}",
                ex, None, v.data)
        if all(a != "none" for a in actions.values()):
            # Every venue dark. This is the one case that still halts globally.
            await self.store.event(
                "risk", "risk",
                "R9-data-staleness: ALL venues stale — halting the whole book",
                data={"actions": actions})
            return None
        logger.info("[carry/bot][health] venues %s | kill switch absent",
                    ", ".join(f"{e}={a}" for e, a in actions.items()))
        return actions

    # ---- 2. risk ----------------------------------------------------------
    async def check_risk(self, venue_actions: dict | None = None) -> None:
        venue_actions = venue_actions or {}
        groups = await self.store.open_groups()
        for g in groups:
            ex, sym = g["exchange"], g["symbol"]
            venue = venue_actions.get(ex, "none")

            if venue == "freeze":
                # We know this venue's data is old. Judging R4/R5 on it would be
                # deciding from a stale book, which is how you exit a healthy
                # position or hold a dead one. Say so and move on.
                await self.store.event(
                    "risk", "risk",
                    "venue data STALE — position held, rules not evaluated on "
                    "stale data (no opens on this venue either)", ex, sym)
                continue

            # perp_mark and spot_price come from DIFFERENT rows on purpose.
            # MEXC perp-only rows (spot endpoint 403'd) carry a real perp mark
            # and a NULL spot price; taking both from one row made neutrality
            # report "prices unavailable" for every MEXC name. The spot mark is
            # taken from the newest row that HAS one, with its age, so a stale
            # spot can be refused rather than silently faking a drift.
            quote = await self.pool.fetchrow(
                """SELECT
                     (SELECT perp_mark FROM funding_basis_snapshots
                       WHERE exchange=$1 AND symbol=$2 AND perp_mark IS NOT NULL
                       ORDER BY ts DESC LIMIT 1) AS perp_mark,
                     (SELECT spot_price FROM funding_basis_snapshots
                       WHERE exchange=$1 AND symbol=$2 AND spot_price IS NOT NULL
                         AND ts > now() - interval '2 days'
                       ORDER BY ts DESC LIMIT 1) AS spot_price,
                     (SELECT extract(epoch FROM (now()-ts))/60.0
                        FROM funding_basis_snapshots
                       WHERE exchange=$1 AND symbol=$2 AND spot_price IS NOT NULL
                         AND ts > now() - interval '2 days'
                       ORDER BY ts DESC LIMIT 1) AS spot_age_min""",
                ex, sym)
            # SPOT MARK SOURCE IS PINNED PER VENUE and never switches
            # mid-position. The age-based fallback this replaces flipped mexc
            # between the REST snapshot and the live book as spot_age crossed
            # 15 min; the two disagree ~0.8% on average (2.1% on BTW), so the
            # flip alone manufactured a >1% "drift" and the rebalance handler
            # paid real money to correct an artifact.
            spot_src = self.cfg.spot_mark_sources.get(ex, "snapshot")
            perp_mark_neutral = float(quote["perp_mark"] or 0.0) if quote else 0.0
            if spot_src == "live-book":
                # BOTH legs from the SAME book at the SAME time. Mixing a live
                # spot mid with a 5-min-stale snapshot perp mark turns the
                # coin's own movement into fake delta.
                spot_mark = await live_mid(self.books, ex, sym, "spot") or 0.0
                perp_live = await live_mid(self.books, ex, sym, "perp") or 0.0
                spot_age = 0.0 if (spot_mark and perp_live) else 1e9
                if perp_live:
                    perp_mark_neutral = perp_live
            else:
                # The snapshot marks both legs on one row, so they are already
                # simultaneous — nothing to reconcile.
                spot_age = float(quote["spot_age_min"] or 1e9) if quote else 1e9
                spot_mark = float(quote["spot_price"] or 0.0) if quote else 0.0
            spot_fresh = bool(spot_mark) and spot_age <= self.cfg.max_data_staleness_min

            if venue == "derisk":
                await self.store.event(
                    "risk", "risk",
                    "venue data DEAD past the hard limit — derisking this "
                    "venue's position (other venues untouched)", ex, sym)
                await self._do_derisk(g, quote, reason="R9-venue-data-dead")
                continue

            verdicts = []
            verdicts.append(await self.risk.funding_flip(
                ex, sym, float(g["interval_hours"] or 8.0)))
            verdicts.append(await self.risk.depth_collapse(
                ex, sym, float(g["notional_usd"]), float(g["entry_depth_usd"] or 0.0)))
            if quote:
                verdicts.append(self.risk.margin(
                    float(g["perp_entry"] or 0.0), float(quote["perp_mark"] or 0.0),
                    float(g["perp_leverage"] or self.cfg.leverage)))
                if spot_fresh:
                    verdicts.append(self.neutral.check(
                        float(g["spot_notional"] or g["notional_usd"]),
                        float(g["spot_entry"] or 0.0),
                        float(g["perp_entry"] or 0.0), spot_mark,
                        perp_mark_neutral,
                        perp_notional=float(g["perp_notional"] or g["notional_usd"])))
                    verdicts[-1].detail += f" [spot mark: {spot_src}]"
                    verdicts[-1].data["spot_source"] = spot_src

                    # ---- SUSTAINED-BREACH GATE -------------------------
                    # A single cycle over the line is noise; three in a row is
                    # drift. The counter lives in memory on purpose: it is a
                    # short-horizon debounce, and a restart costing us three
                    # extra cycles of patience is the safe direction to err.
                    v = verdicts[-1]
                    gid = g["group_id"]
                    if v.fired:
                        n = self._neutrality_breaches.get(gid, 0) + 1
                        self._neutrality_breaches[gid] = n
                        need = self.cfg.rebalance_confirm_cycles
                        if n < need:
                            v.fired, v.action = False, "none"
                            v.detail += (f" — breach {n}/{need}, holding "
                                         f"(not yet sustained)")
                    else:
                        self._neutrality_breaches.pop(gid, None)
                else:
                    # The PINNED source is unavailable. We do NOT quietly fall
                    # back to the other one — that substitution is the bug this
                    # release removes. Say so and leave the rule unevaluated.
                    verdicts.append(Verdict(
                        "neutrality", False, "none",
                        f"pinned spot source '{spot_src}' unavailable "
                        f"(age {spot_age:.0f}min, limit "
                        f"{self.cfg.max_data_staleness_min:.0f}min) — NOT "
                        f"evaluated; refusing to substitute the other source",
                        {"spot_age_min": spot_age, "spot_source": spot_src}))
            # log EVERY rule, fired or not — quiet must be visibly tested
            for v in verdicts:
                await self.store.event(
                    "risk" if v.fired else "info", "risk",
                    f"{v.rule} {'FIRED->' + v.action if v.fired else 'ok'}: {v.detail}",
                    ex, sym, v.data)

            # ---- REMEDIATION: a fired rule now TAKES ITS ACTION -------------
            # Ordered by severity. Exit wins; otherwise derisk, then topup, then
            # rebalance. Only one structural change per position per tick, so a
            # handler's own effect is measurable on the next pass.
            if not any(v.fired and v.action == "exit" for v in verdicts):
                acted = False
                for v in verdicts:
                    if not v.fired or acted:
                        continue
                    if v.action == "derisk":
                        await self._do_derisk(g, quote, reason=v.rule)
                        acted = True
                    elif v.action == "topup":
                        await self._do_topup(g, quote, verdict=v)
                        acted = True
                    elif v.action == "rebalance":
                        await self._do_rebalance(g, quote, verdict=v,
                                                 spot_mark=spot_mark,
                                                 spot_src=spot_src,
                                                 perp_mark=perp_mark_neutral)
                        acted = True

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

    # ---- 2b. REMEDIATION HANDLERS -----------------------------------------
    # Each logs FIRED -> action -> RESULT with the metric before and after, so a
    # quiet rule reads as REMEDIATED rather than merely detected.

    async def _do_rebalance(self, g, quote, verdict, spot_mark: float = 0.0,
                            spot_src: str = "snapshot",
                            perp_mark: float = 0.0) -> None:
        """Neutrality: resize the perp leg until the two legs' mark values match."""
        ex, sym = g["exchange"], g["symbol"]
        if not quote:
            await self.store.event("warn", "remediate",
                                   "neutrality rebalance SKIPPED: no quote",
                                   ex, sym)
            return
        spot_now = float(spot_mark or quote["spot_price"] or 0.0)
        perp_now = float(perp_mark or quote["perp_mark"] or 0.0)
        spot_entry = float(g["spot_entry"] or 0.0)
        perp_entry = float(g["perp_entry"] or 0.0)
        cur_perp = float(g["perp_notional"] or g["notional_usd"])
        spot_notional = float(g["spot_notional"] or g["notional_usd"])
        if not (spot_now and perp_now and spot_entry and perp_entry):
            await self.store.event("warn", "remediate",
                                   "neutrality rebalance SKIPPED: prices unusable",
                                   ex, sym)
            return

        # Correct to the DEADBAND EDGE on the same side, not to exact zero:
        # a 1.2% breach becomes a 0.9pp correction instead of 1.2pp, so every
        # rebalance trades less. Re-trigger protection comes from the
        # consecutive-cycle gate, not from this target.
        before_pct = float(verdict.data.get("delta_pct", 0.0))
        band = self.cfg.rebalance_deadband_pct
        target_pct = band if before_pct > 0 else -band
        target = self.risk.neutral_perp_notional(
            spot_notional, spot_entry, perp_entry, spot_now, perp_now,
            target_delta_pct=target_pct)
        delta_notional = target - cur_perp
        cap = self.cfg.max_rebalance_fraction * cur_perp
        if abs(delta_notional) > cap:                 # never resize violently
            delta_notional = cap if delta_notional > 0 else -cap
            target = cur_perp + delta_notional
        traded_usd = abs(delta_notional) * (perp_now / perp_entry)
        # growing the SHORT means selling more perp (hit the bid); shrinking it
        # means buying perp back (lift the ask)
        direction = "sell" if delta_notional > 0 else "buy"
        price, cost, note = await self.exec.trade_leg(
            ex, sym, "perp", traded_usd, direction)
        await self.store.adjust_perp_notional(g["group_id"], target, cost)

        before = before_pct
        after = self.neutral.check(spot_notional, spot_entry, perp_entry,
                                   spot_now, perp_now, perp_notional=target)
        # The breach is answered; start counting afresh.
        self._neutrality_breaches.pop(g["group_id"], None)
        await self.store.event(
            "risk", "remediate",
            f"neutrality FIRED->rebalance->DONE: perp notional "
            f"${cur_perp:,.2f} -> ${target:,.2f} ({direction} ${traded_usd:,.2f}, "
            f"cost ${cost:.4f}; {note}) | delta {before:+.2f}% -> "
            f"{after.data.get('delta_pct', 0.0):+.2f}% "
            f"(band +/-{band:.2f}%, threshold "
            f"{self.cfg.rebalance_delta_pct:.2f}%, confirmed over "
            f"{self.cfg.rebalance_confirm_cycles} cycles) [spot: {spot_src}]",
            ex, sym,
            {"before_pct": before, "after_pct": after.data.get("delta_pct"),
             "old_notional": cur_perp, "new_notional": target,
             "cost_usd": cost, "spot_source": spot_src,
             "target_pct": target_pct})

    async def _do_topup(self, g, quote, verdict) -> None:
        """R2/R3: post more margin, i.e. deleverage, until the buffer is back."""
        ex, sym = g["exchange"], g["symbol"]
        perp_entry = float(g["perp_entry"] or 0.0)
        perp_now = float(quote["perp_mark"] or 0.0) if quote else 0.0
        if not (perp_entry and perp_now):
            await self.store.event("warn", "remediate",
                                   "margin topup SKIPPED: no mark", ex, sym)
            return
        cur_lev = float(g["perp_leverage"] or self.cfg.leverage)
        new_lev = self.risk.target_leverage(perp_entry, perp_now)
        if new_lev >= cur_lev:
            await self.store.event("warn", "remediate",
                                   "margin topup SKIPPED: already deleveraged "
                                   f"({cur_lev:.2f}x)", ex, sym)
            return
        notional = float(g["perp_notional"] or g["notional_usd"])
        margin_added = notional / new_lev - notional / cur_lev
        await self.store.set_leverage(g["group_id"], new_lev, margin_added)

        after = self.risk.margin(perp_entry, perp_now, new_lev)
        await self.store.event(
            "risk", "remediate",
            f"R2-margin FIRED->topup->DONE: posted ${margin_added:,.2f} extra "
            f"margin, effective leverage {cur_lev:.2f}x -> {new_lev:.2f}x | "
            f"buffer {verdict.data.get('buffer_pp', 0.0):.1f}pp -> "
            f"{after.data.get('buffer_pp', 0.0):.1f}pp",
            ex, sym,
            {"margin_added_usd": margin_added, "old_leverage": cur_lev,
             "new_leverage": new_lev,
             "before_buffer_pp": verdict.data.get("buffer_pp"),
             "after_buffer_pp": after.data.get("buffer_pp")})

    async def _do_derisk(self, g, quote, reason: str) -> None:
        """Past the derisk line: close part of BOTH legs.

        Both legs, always. Closing one side converts a hedged carry into the
        naked directional position this strategy exists to avoid — which would
        be a far worse outcome than the margin breach we are fixing.
        """
        ex, sym = g["exchange"], g["symbol"]
        perp_entry = float(g["perp_entry"] or 0.0)
        perp_now = float(quote["perp_mark"] or 0.0) if quote else 0.0
        cur_lev = float(g["perp_leverage"] or self.cfg.leverage)
        notional = float(g["notional_usd"])

        frac = self.cfg.max_derisk_fraction
        if perp_entry and perp_now:
            want_lev = self.risk.target_leverage(perp_entry, perp_now)
            # margin stays posted against a smaller position: L_eff = L * keep
            keep_needed = max(0.0, min(1.0, want_lev / cur_lev)) if cur_lev else 0.5
            frac = min(self.cfg.max_derisk_fraction, 1.0 - keep_needed)
        frac = max(0.05, frac)
        keep = 1.0 - frac

        closed_usd = notional * frac
        _, c1, n1 = await self.exec.trade_leg(ex, sym, "spot", closed_usd, "sell")
        _, c2, n2 = await self.exec.trade_leg(ex, sym, "perp", closed_usd, "buy")
        cost = c1 + c2
        new_lev = cur_lev * keep
        await self.store.partial_close(g["group_id"], keep, cost, new_lev)

        before = self.risk.margin(perp_entry, perp_now, cur_lev)
        after = self.risk.margin(perp_entry, perp_now, new_lev)
        await self.store.event(
            "risk", "remediate",
            f"{reason} FIRED->derisk->DONE: closed {frac:.0%} of BOTH legs "
            f"(${closed_usd:,.2f}/leg, cost ${cost:.4f}; {n1}; {n2}), "
            f"effective leverage {cur_lev:.2f}x -> {new_lev:.2f}x | buffer "
            f"{before.data.get('buffer_pp', 0.0):.1f}pp -> "
            f"{after.data.get('buffer_pp', 0.0):.1f}pp",
            ex, sym,
            {"closed_fraction": frac, "closed_usd": closed_usd, "cost_usd": cost,
             "old_leverage": cur_lev, "new_leverage": new_lev,
             "before_buffer_pp": before.data.get("buffer_pp"),
             "after_buffer_pp": after.data.get("buffer_pp")})

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
    async def rebalance_basket(self, venue_actions: dict | None = None) -> None:
        venue_actions = venue_actions or {}
        # Peak RSS is set by the cache cap, not by how many names the universe
        # holds — see BookSource. Drop the previous pass's curves first.
        self.books.reset_cache()
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
        frozen = {ex for ex, a in venue_actions.items() if a != "none"}
        if frozen:
            logger.info("[carry/bot][select] no opens on stale venue(s): %s",
                        ", ".join(sorted(frozen)))
        fresh = [c for c in ranked
                 if c.key not in held and c.ex not in frozen]
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
        venue_actions = await self.health()
        if venue_actions is None:
            return {"halted": True}
        await self.check_risk(venue_actions)
        n = await self.accrue()
        if n:
            logger.info("[carry/bot][accrue] %d funding epoch(s) accrued", n)
        await self.rebalance_basket(venue_actions)
        self.books.reset_cache()          # do not hold curves between cycles
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
                va = await bot.health()
                if va is not None:
                    await bot.check_risk(va)
                    if await bot.accrue():
                        await bot.report()
                    bot.books.reset_cache()
        except Exception as exc:
            logger.exception("[carry/bot] cycle failed: %r", exc)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=cfg.tick_secs)

    logger.info("[carry/bot] shutting down…")
    await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
