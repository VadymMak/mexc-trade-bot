"""Persistence for the carry bot — ADDITIVE, self-healing.

Two new tables, both CREATE TABLE IF NOT EXISTS, both touched by nothing else:
    paper_carry_positions — one row per LEG (spot and perp) of a paper position
    paper_carry_events    — the runner log: selections, accruals, risk triggers

Touches no existing table. The arb archive (ml_trade_outcomes, paper_positions,
spread_observations) and the ёрш tables are not read or written here. Note that
`paper_positions` is the OLD arbitrage table and is deliberately NOT reused —
it has a known schema bug and belongs to a dead strategy.
"""
from __future__ import annotations

import json
import logging

from . import basis

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_carry_positions (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                TEXT,
    group_id              TEXT,              -- both legs of one carry share this
    opened_ts             TIMESTAMPTZ DEFAULT now(),
    exchange              TEXT,
    symbol                TEXT,
    leg                   TEXT,              -- 'spot' | 'perp'
    side                  TEXT,              -- 'long' | 'short'
    notional_usd          DOUBLE PRECISION,
    entry_price           DOUBLE PRECISION,
    leverage              DOUBLE PRECISION,
    status                TEXT,              -- 'open' | 'closed'
    closed_ts             TIMESTAMPTZ,
    close_price           DOUBLE PRECISION,
    realised_funding_usd  DOUBLE PRECISION DEFAULT 0,
    modelled_funding_usd  DOUBLE PRECISION DEFAULT 0,
    entry_cost_usd        DOUBLE PRECISION DEFAULT 0,
    exit_cost_usd         DOUBLE PRECISION DEFAULT 0,
    paper_pnl_usd         DOUBLE PRECISION DEFAULT 0,
    last_epoch            BIGINT,
    interval_hours        DOUBLE PRECISION,
    entry_depth_usd       DOUBLE PRECISION,  -- worst-hour capacity at entry (R5)
    depth_basis           TEXT,              -- 'worst-hour' | 'p10-limited'
    notes                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_pcp_status ON paper_carry_positions (status, exchange, symbol);
CREATE INDEX IF NOT EXISTS idx_pcp_group  ON paper_carry_positions (group_id);

-- Remediation bookkeeping (2026-08-23). Nullable, no DEFAULT => metadata-only.
ALTER TABLE paper_carry_positions
    ADD COLUMN IF NOT EXISTS margin_added_usd  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS remediation_cost_usd DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS remediations      INTEGER;

-- The BASIS LEG (2026-09-04). Until now `close_price` was NULL on every closed
-- leg and the carry's second P&L component was never booked at all. INPUTS
-- BESIDE OUTPUTS: the two marks, their sample counts, their timestamps and
-- their provenance all sit next to the dollar figure they produce, so a future
-- session can audit the number without re-deriving it. That rule would have
-- caught the funding-interval bug two weeks earlier.
ALTER TABLE paper_carry_positions
    ADD COLUMN IF NOT EXISTS entry_basis_bps      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exit_basis_bps       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS entry_basis_ts       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS exit_basis_ts        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS entry_basis_n        INTEGER,
    ADD COLUMN IF NOT EXISTS exit_basis_n         INTEGER,
    ADD COLUMN IF NOT EXISTS basis_mark_source    TEXT,
    ADD COLUMN IF NOT EXISTS basis_pnl_usd        DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS funding_only_pnl_usd DOUBLE PRECISION;

-- The OLD series, preserved across the redefinition. Every row written before
-- 2026-09-04 had paper_pnl_usd == funding-minus-costs by construction, so
-- seeding funding_only_pnl_usd from it is exact, not an estimate. From here on
-- paper_pnl_usd = funding_only_pnl_usd + basis_pnl_usd and the two series are
-- comparable ACROSS the break rather than one silently changing meaning inside
-- the window.
UPDATE paper_carry_positions
   SET funding_only_pnl_usd = paper_pnl_usd
 WHERE funding_only_pnl_usd IS NULL;

CREATE TABLE IF NOT EXISTS paper_carry_events (
    id        BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ DEFAULT now(),
    run_id    TEXT,
    level     TEXT,      -- 'info' | 'warn' | 'risk'
    kind      TEXT,      -- 'select' | 'open' | 'accrue' | 'risk' | 'close' | 'health'
    exchange  TEXT,
    symbol    TEXT,
    message   TEXT,
    data      JSONB
);
CREATE INDEX IF NOT EXISTS idx_pce_ts ON paper_carry_events (ts DESC);

-- Re-entry cooldown (2026-08-23). A name exited on a funding flip is exiled
-- here, so the ban SURVIVES A RESTART — an in-memory set would have been
-- cleared by the very restart a bad cycle tends to cause. `exits` counts
-- repeat offenders: each re-offence lengthens the next exile.
CREATE TABLE IF NOT EXISTS carry_reentry_blocks (
    exchange   TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    blocked_ts TIMESTAMPTZ DEFAULT now(),
    until_ts   TIMESTAMPTZ NOT NULL,
    reason     TEXT,
    exits      INTEGER DEFAULT 1,
    PRIMARY KEY (exchange, symbol)
);
CREATE INDEX IF NOT EXISTS idx_crb_until ON carry_reentry_blocks (until_ts);
"""


class BotStore:
    def __init__(self, pool, run_id: str) -> None:
        self._pool = pool
        self.run_id = run_id

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA)
        logger.info("[carry/bot] schema ready (paper_carry_positions, paper_carry_events)")

    # ---- events -----------------------------------------------------------
    async def event(self, level: str, kind: str, message: str,
                    ex: str | None = None, sym: str | None = None,
                    data: dict | None = None) -> None:
        await self._pool.execute(
            """INSERT INTO paper_carry_events
                   (run_id, level, kind, exchange, symbol, message, data)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            self.run_id, level, kind, ex, sym, message,
            json.dumps(data or {}, default=str))
        log = logger.warning if level in ("warn", "risk") else logger.info
        tag = f" {ex}/{sym}" if sym else ""
        log("[carry/bot][%s]%s %s", kind, tag, message)

    # ---- positions --------------------------------------------------------
    async def open_leg(self, group_id: str, ex: str, sym: str, leg: str, side: str,
                       notional_usd: float, entry_price: float, leverage: float,
                       entry_cost_usd: float, interval_hours: float,
                       last_epoch: int, entry_depth_usd: float,
                       depth_basis: str, notes: str,
                       entry_basis=None) -> int:
        """`entry_basis` is a basis.BasisMark, stored on BOTH legs so either row
        is self-describing. An unmarked entry stores NULL, never 0 bps: a
        missing mark and a flat basis must stay distinguishable."""
        eb = entry_basis if (entry_basis is not None and entry_basis.ok) else None
        row = await self._pool.fetchrow(
            """INSERT INTO paper_carry_positions
                 (run_id, group_id, exchange, symbol, leg, side, notional_usd,
                  entry_price, leverage, status, entry_cost_usd, interval_hours,
                  last_epoch, entry_depth_usd, depth_basis, notes,
                  paper_pnl_usd, funding_only_pnl_usd,
                  entry_basis_bps, entry_basis_ts, entry_basis_n,
                  basis_mark_source)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open',$10,$11,$12,$13,$14,$15,
                       $16,$16,$17,$18,$19,$20)
               RETURNING id""",
            self.run_id, group_id, ex, sym, leg, side, notional_usd, entry_price,
            leverage, entry_cost_usd, interval_hours, last_epoch, entry_depth_usd,
            depth_basis, notes, -entry_cost_usd,
            (eb.bps if eb else None), (eb.last_ts if eb else None),
            (eb.n if eb else None),
            (eb.source if entry_basis is not None else None))
        return row["id"]

    async def open_positions(self) -> list:
        return await self._pool.fetch(
            """SELECT * FROM paper_carry_positions
               WHERE status='open' AND run_id=$1 ORDER BY group_id, leg""",
            self.run_id)

    async def open_groups(self) -> list:
        """One row per open carry (the perp leg carries the funding)."""
        return await self._pool.fetch(
            """SELECT group_id, exchange, symbol,
                      max(interval_hours)                       AS interval_hours,
                      max(last_epoch)                           AS last_epoch,
                      max(notional_usd)                         AS notional_usd,
                      max(notional_usd) FILTER (WHERE leg='spot') AS spot_notional,
                      max(notional_usd) FILTER (WHERE leg='perp') AS perp_notional,
                      max(leverage)    FILTER (WHERE leg='perp')  AS perp_leverage,
                      max(entry_depth_usd)                      AS entry_depth_usd,
                      min(opened_ts)                            AS opened_ts,
                      sum(realised_funding_usd)                 AS realised_funding,
                      sum(modelled_funding_usd)                 AS modelled_funding,
                      sum(paper_pnl_usd)                        AS pnl,
                      sum(entry_cost_usd)                       AS entry_cost,
                      max(entry_price) FILTER (WHERE leg='spot') AS spot_entry,
                      max(entry_price) FILTER (WHERE leg='perp') AS perp_entry,
                      sum(funding_only_pnl_usd)                 AS funding_only_pnl,
                      sum(basis_pnl_usd)                        AS basis_pnl,
                      max(entry_basis_bps)                      AS entry_basis_bps,
                      max(basis_mark_source)                    AS basis_mark_source,
                      max(leverage)                             AS leverage
               FROM paper_carry_positions
               WHERE status='open' AND run_id=$1
               GROUP BY group_id, exchange, symbol""",
            self.run_id)

    async def accrue(self, group_id: str, realised_usd: float,
                     modelled_usd: float, epoch: int) -> None:
        """Funding lands on the PERP leg — that is the leg that pays/receives."""
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET realised_funding_usd = realised_funding_usd + $2,
                   modelled_funding_usd = modelled_funding_usd + $3,
                   paper_pnl_usd        = paper_pnl_usd + $2,
                   funding_only_pnl_usd = funding_only_pnl_usd + $2,
                   last_epoch           = $4
               WHERE group_id=$1 AND leg='perp' AND status='open'""",
            group_id, realised_usd, modelled_usd, epoch)
        await self._pool.execute(
            """UPDATE paper_carry_positions SET last_epoch=$2
               WHERE group_id=$1 AND status='open'""", group_id, epoch)

    async def close_group(self, group_id: str, exit_cost_usd: float,
                          reason: str, exit_basis=None,
                          spot_close_price: float | None = None,
                          perp_close_price: float | None = None) -> dict:
        """Close both legs and BOOK THE BASIS LEG. Returns the booked terms.

        MID-TO-MID, COSTS SEPARATE (see basis.py). The basis term is struck
        from mid marks that contain no spread, and `exit_cost_usd` is charged
        once, on its own line. Booking `close_price - entry_price` here instead
        would have charged the round-trip spread twice — the fills are struck
        at the ask and the bid — and welded that double-count into the series
        permanently. `tests/test_basis_booking.py` fails if it reappears.

        The basis P&L lands on the SPOT leg (funding lands on the perp leg), so
        the two components stay separable per row. The exposure is the SPOT
        notional: after a neutrality rebalance the legs differ on purpose, and
        the unmatched residual is directional risk, not basis.

        `close_price` is finally populated on both legs. It is the executable
        exit price, recorded as an INPUT beside the marks — and deliberately
        NOT what the P&L is struck from.
        """
        xb = exit_basis if (exit_basis is not None and exit_basis.ok) else None
        ent = await self._pool.fetchrow(
            """SELECT max(entry_basis_bps)                        AS entry_bps,
                      max(notional_usd) FILTER (WHERE leg='spot') AS spot_notional
               FROM paper_carry_positions
               WHERE group_id=$1 AND status='open'""", group_id)
        entry_bps = float(ent["entry_bps"]) if ent and ent["entry_bps"] is not None else None
        notional = float(ent["spot_notional"] or 0.0) if ent else 0.0
        exit_bps = xb.bps if xb else None
        # The SAME pure function the test exercises. A basis term computed one
        # way in the engine and another way in the test proves nothing.
        pnl_basis = basis.basis_pnl_usd(notional, entry_bps, exit_bps)

        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET status='closed', closed_ts=now(),
                   exit_cost_usd  = $2,
                   close_price    = CASE WHEN leg='spot' THEN $5 ELSE $6 END,
                   exit_basis_bps = $4,
                   exit_basis_ts  = $7,
                   exit_basis_n   = $8,
                   basis_mark_source = coalesce(basis_mark_source,'?') || ' -> ' || $9,
                   -- $10 MUST be cast. Without it Postgres infers the
                   -- parameter's type from the `0` literal in the ELSE arm,
                   -- makes it INTEGER, and silently truncates every basis
                   -- figure under a dollar to zero. The backfill's two-way
                   -- reconciliation caught exactly that on 2026-09-04.
                   basis_pnl_usd  = CASE WHEN leg='spot'
                                         THEN $10::double precision ELSE 0 END,
                   funding_only_pnl_usd = funding_only_pnl_usd - $2,
                   paper_pnl_usd  = paper_pnl_usd - $2
                                    + CASE WHEN leg='spot'
                                           THEN $10::double precision ELSE 0 END,
                   notes = coalesce(notes,'') || ' | closed: ' || $3
               WHERE group_id=$1 AND status='open'""",
            group_id, exit_cost_usd / 2.0, reason, exit_bps,
            spot_close_price, perp_close_price,
            (xb.last_ts if xb else None), (xb.n if xb else None),
            (xb.source if exit_basis is not None else "unmarked"),
            pnl_basis)
        return {"basis_pnl_usd": pnl_basis, "entry_basis_bps": entry_bps,
                "exit_basis_bps": exit_bps, "notional_usd": notional,
                "marked": entry_bps is not None and exit_bps is not None}

    # ---- remediation (2026-08-23) -----------------------------------------
    # Every handler below CHANGES THE POSITION. Until now a fired rule only
    # logged: neutrality drifted to 2x its threshold and a margin buffer stayed
    # breached for 32h because "rebalance"/"topup"/"derisk" had no handler at
    # all. Detection without action is worse than no rule — it reads as safe.

    async def adjust_perp_notional(self, group_id: str, new_notional_usd: float,
                                   cost_usd: float) -> None:
        """Resize the perp leg (neutrality rebalance). Cost is a real paper
        debit — a rebalance is a trade, not a free correction."""
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET notional_usd         = $2,
                   paper_pnl_usd        = paper_pnl_usd - $3,
                   funding_only_pnl_usd = funding_only_pnl_usd - $3,
                   remediation_cost_usd = coalesce(remediation_cost_usd,0) + $3,
                   remediations         = coalesce(remediations,0) + 1
               WHERE group_id=$1 AND leg='perp' AND status='open'""",
            group_id, new_notional_usd, cost_usd)

    async def set_leverage(self, group_id: str, new_leverage: float,
                           margin_added_usd: float) -> None:
        """Margin top-up, modelled as the deleveraging it actually is: posting
        more margin against the same position lowers effective leverage, which
        is what moves the liquidation price away."""
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET leverage         = $2,
                   margin_added_usd = coalesce(margin_added_usd,0) + $3,
                   remediations     = coalesce(remediations,0) + 1
               WHERE group_id=$1 AND leg='perp' AND status='open'""",
            group_id, new_leverage, margin_added_usd)

    async def partial_close(self, group_id: str, keep_fraction: float,
                            cost_usd: float, new_leverage: float) -> None:
        """Close (1-keep) of BOTH legs. Both, always — closing one side would
        turn a hedged position into the naked directional bet this strategy
        exists to avoid. Margin stays posted against the smaller position, so
        effective leverage falls and the buffer is restored."""
        await self._pool.execute(
            """UPDATE paper_carry_positions
               SET notional_usd         = notional_usd * $2,
                   paper_pnl_usd        = paper_pnl_usd - $3,
                   funding_only_pnl_usd = funding_only_pnl_usd - $3,
                   remediation_cost_usd = coalesce(remediation_cost_usd,0) + $3,
                   remediations         = coalesce(remediations,0) + 1,
                   leverage             = CASE WHEN leg='perp' THEN $4
                                               ELSE leverage END
               WHERE group_id=$1 AND status='open'""",
            group_id, keep_fraction, cost_usd / 2.0, new_leverage)

    # ---- re-entry cooldown -------------------------------------------------
    async def block_reentry(self, ex: str, sym: str, base_hours: float,
                            reason: str, max_stacks: int) -> tuple[float, int]:
        """Exile a name from selection. Repeat offenders serve longer:
        base x min(exits, max_stacks). Returns (hours_served, exits)."""
        row = await self._pool.fetchrow(
            """INSERT INTO carry_reentry_blocks
                   (exchange, symbol, blocked_ts, until_ts, reason, exits)
               VALUES ($1,$2, now(), now() + ($3 || ' hours')::interval, $4, 1)
               ON CONFLICT (exchange, symbol) DO UPDATE SET
                   blocked_ts = now(),
                   exits      = carry_reentry_blocks.exits + 1,
                   reason     = EXCLUDED.reason,
                   until_ts   = now() + (
                       $3::double precision
                       * LEAST(carry_reentry_blocks.exits + 1, $5::int)
                       || ' hours')::interval
               RETURNING exits,
                         extract(epoch FROM (until_ts - now()))/3600.0 AS hours""",
            ex, sym, str(base_hours), reason, max_stacks)
        return float(row["hours"]), int(row["exits"])

    async def reentry_blocks(self) -> dict:
        """Active blocks only. Expired rows are KEPT so `exits` still remembers
        the name's history the next time it misbehaves."""
        rows = await self._pool.fetch(
            """SELECT exchange, symbol, until_ts, reason, exits,
                      extract(epoch FROM (until_ts - now()))/3600.0 AS hours_left
               FROM carry_reentry_blocks WHERE until_ts > now()""")
        return {(r["exchange"], r["symbol"]): dict(r) for r in rows}

    async def recently_exited(self, days: float) -> set:
        """Names flip-exited inside the hysteresis memory. These face the HIGH
        re-entry bar even after their hard cooldown has expired."""
        rows = await self._pool.fetch(
            """SELECT DISTINCT exchange, symbol FROM carry_reentry_blocks
               WHERE blocked_ts > now() - ($1 || ' days')::interval""",
            str(days))
        return {(r["exchange"], r["symbol"]) for r in rows}

    # ---- reporting --------------------------------------------------------
    async def summary(self) -> dict:
        row = await self._pool.fetchrow(
            """SELECT count(DISTINCT group_id) FILTER (WHERE status='open')   AS open_groups,
                      count(DISTINCT group_id) FILTER (WHERE status='closed') AS closed_groups,
                      coalesce(sum(notional_usd) FILTER (WHERE status='open' AND leg='spot'),0) AS notional,
                      coalesce(sum(realised_funding_usd),0) AS realised,
                      coalesce(sum(modelled_funding_usd),0) AS modelled,
                      coalesce(sum(paper_pnl_usd),0)        AS pnl,
                      coalesce(sum(funding_only_pnl_usd),0)  AS funding_only_pnl,
                      coalesce(sum(basis_pnl_usd),0)         AS basis_pnl,
                      count(*) FILTER (WHERE status='closed' AND leg='spot'
                                       AND exit_basis_bps IS NOT NULL
                                       AND entry_basis_bps IS NOT NULL) AS basis_marked,
                      count(*) FILTER (WHERE status='closed' AND leg='spot') AS closed_spot,
                      min(opened_ts)                        AS first_open
               FROM paper_carry_positions WHERE run_id=$1""", self.run_id)
        return dict(row) if row else {}
