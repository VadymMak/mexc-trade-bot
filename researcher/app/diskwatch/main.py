"""
Disk-space monitor — STANDALONE, ADDITIVE, READ-ONLY except for its own table.

WHY IT EXISTS
    Seven collectors now write continuously (carry_book_l2 alone is 16 GiB and
    growing). If the root filesystem fills, Postgres stops accepting writes and
    every service on the box fails at once — the paper bot, all the collectors,
    the backend. That failure is silent right up to the moment it is total.
    This monitor makes it loud, early, with a number of days attached.

WHAT IT TOUCHES
    Writes `disk_health` and NOTHING else. It READS os.statvfs, /proc/mounts,
    `vgs` and Postgres catalogue views (pg_class / pg_database_size). It does
    not read the contents of any collector table, does not write to one, and
    does not restart, signal or reconfigure any service. No API keys, no
    exchange calls, no network egress at all.

UNITS
    Every *_gb column is GiB (1024^3), matching what `df -h`, `vgs` and every
    prior note about this box report. Named _gb rather than _gib only because
    that is the agreed schema; the docstring is the authority.

LOUD, NOT SILENT (the perp WS unit once stayed "active" for 3.4 days while dead)
    - Any unhandled failure logs an exception AND exits non-zero, so systemd
      marks the unit failed and `systemctl list-timers` stops looking healthy.
    - A partial failure (e.g. `vgs` unavailable) records NULL and logs a
      WARNING rather than substituting a plausible number.
    - Every projected figure carries the name of the estimator that produced
      it (`days_to_full_basis`, and `basis` per collector), so no reader has to
      guess whether a growth rate came from real history or a bootstrap.

Run:  cd researcher && .venv/bin/python -m app.diskwatch.main
      cd researcher && .venv/bin/python -m app.diskwatch.main --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import asyncpg

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

LOG_PATH = os.getenv("DISKWATCH_LOG",
                     "/home/vadym/mexc-trade-bot/researcher/disk_monitor.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("diskwatch")

DSN = os.getenv("NEON_DATABASE_URL", "")
GIB = 1024.0 ** 3

# Alert thresholds. Two tiers: WARNING is "plan the cleanup", CRITICAL is
# "act now or Postgres stops writing".
WARN_FREE_PCT = float(os.getenv("DISKWATCH_WARN_FREE_PCT", "15"))
CRIT_FREE_PCT = float(os.getenv("DISKWATCH_CRIT_FREE_PCT", "8"))
WARN_DAYS = float(os.getenv("DISKWATCH_WARN_DAYS", "30"))
CRIT_DAYS = float(os.getenv("DISKWATCH_CRIT_DAYS", "10"))

# Samples used for the free-space slope. 8 x 30min = 4h of history.
SLOPE_SAMPLES = int(os.getenv("DISKWATCH_SLOPE_SAMPLES", "8"))
# Below this span the slope is dominated by WAL/vacuum jitter, not by growth.
SLOPE_MIN_SPAN_HOURS = float(os.getenv("DISKWATCH_SLOPE_MIN_SPAN_H", "2"))

REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "vfat", "zfs", "f2fs"}

# The write-heavy tables. paper_carry_* is expanded by prefix so a new one is
# picked up without editing this list.
TRACKED = ("carry_book_l2", "funding_basis_snapshots", "venue_funding_snapshots",
           "basis_snapshots", "tape_prints", "ersh_book_l2", "book_ticker",
           "spread_observations", "bybit_funding_snapshots")
TRACKED_PREFIXES = ("paper_carry_",)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS disk_health (
    id                 BIGSERIAL PRIMARY KEY,
    ts                 TIMESTAMPTZ DEFAULT now(),
    mount              TEXT,
    device             TEXT,
    size_gb            DOUBLE PRECISION,   -- GiB
    used_gb            DOUBLE PRECISION,   -- GiB
    free_gb            DOUBLE PRECISION,   -- GiB
    used_pct           DOUBLE PRECISION,
    free_pct           DOUBLE PRECISION,
    vg_name            TEXT,
    vg_free_gb         DOUBLE PRECISION,   -- GiB unallocated in the LVM VG
    is_db_mount        BOOLEAN,
    db_size_gb         DOUBLE PRECISION,   -- GiB, NULL on non-DB mounts
    top_tables         JSONB,
    collectors         JSONB,
    free_gb_per_day    DOUBLE PRECISION,   -- >0 means free space is shrinking
    days_to_full       DOUBLE PRECISION,
    days_to_full_basis TEXT,
    samples_used       INTEGER,
    status             TEXT,               -- ok | warning | critical
    alerts             TEXT
);
CREATE INDEX IF NOT EXISTS idx_dh_ts ON disk_health (ts DESC);
CREATE INDEX IF NOT EXISTS idx_dh_mount_ts ON disk_health (mount, ts DESC);
"""

INSERT_SQL = """
INSERT INTO disk_health
 (mount, device, size_gb, used_gb, free_gb, used_pct, free_pct, vg_name,
  vg_free_gb, is_db_mount, db_size_gb, top_tables, collectors,
  free_gb_per_day, days_to_full, days_to_full_basis, samples_used, status, alerts)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
"""


# ── host facts ──────────────────────────────────────────────────────────────
def mounts() -> list[dict]:
    """Real block-device mounts only, deduped by mountpoint.

    Reads /proc/mounts rather than shelling out to df: no parsing of localised
    output, and a bind mount cannot show up twice as if it were extra capacity.
    """
    out, seen = [], set()
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        log.error("cannot read /proc/mounts: %r", exc)
        return out
    for line in lines:
        p = line.split()
        if len(p) < 3:
            continue
        dev, mp, fs = p[0], p[1].replace("\\040", " "), p[2]
        if fs not in REAL_FS or not dev.startswith("/dev/") or mp in seen:
            continue
        try:
            u = shutil.disk_usage(mp)
        except OSError as exc:
            log.warning("statvfs failed for %s: %r", mp, exc)
            continue
        seen.add(mp)
        # used+free < total: the ext4 root-reserve is neither used nor available
        # to us. free_pct is computed against total so it matches `df`.
        out.append({"mount": mp, "device": dev,
                    "size_gb": u.total / GIB, "used_gb": u.used / GIB,
                    "free_gb": u.free / GIB,
                    "used_pct": 100.0 * u.used / u.total if u.total else None,
                    "free_pct": 100.0 * u.free / u.total if u.total else None})
    return out


def vg_free() -> tuple[str | None, float | None]:
    """LVM unallocated space — the headroom to GROW the filesystem.

    Needs root, so it goes through `sudo -n`. On any failure this returns
    (None, None) and logs a WARNING: a monitor that invents a headroom number
    is worse than one that admits it does not know.
    """
    try:
        r = subprocess.run(
            ["sudo", "-n", "/usr/sbin/vgs", "--noheadings", "--nosuffix",
             "--units", "b", "-o", "vg_name,vg_free"],
            capture_output=True, text=True, timeout=20, check=True)
    except Exception as exc:                          # noqa: BLE001
        log.warning("vg_free unavailable (%r) — recording NULL, not a guess", exc)
        return None, None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                return parts[0], float(parts[1]) / GIB
            except ValueError:
                continue
    log.warning("vgs returned no parseable row: %r — recording NULL", r.stdout)
    return None, None


def db_mount(mps: list[str]) -> str | None:
    """Which mount holds the Postgres data directory.

    SHOW data_directory needs pg_read_all_settings, which our role does not
    have, so the path is probed on disk instead of asked for.
    """
    for cand in ("/var/lib/postgresql", "/var/lib/pgsql"):
        if os.path.isdir(cand):
            best = max((m for m in mps if cand.startswith(m)), key=len, default=None)
            if best:
                return best
    return "/" if "/" in mps else None


# ── database facts ──────────────────────────────────────────────────────────
async def db_stats(con) -> dict:
    db_size = await con.fetchval(
        "SELECT pg_database_size(current_database())::float8")
    rows = await con.fetch("""
        SELECT c.relname AS name,
               pg_total_relation_size(c.oid)::float8 AS bytes,
               c.reltuples::float8 AS approx_rows
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC""")
    top = [{"name": r["name"], "gb": round(r["bytes"] / GIB, 4)} for r in rows[:10]]
    sizes = {r["name"]: (r["bytes"],
                         None if r["approx_rows"] is None or r["approx_rows"] < 0
                         else int(r["approx_rows"])) for r in rows}
    names = [n for n in sizes
             if n in TRACKED or n.startswith(TRACKED_PREFIXES)]

    # Lifetime span per table, used only as the FIRST-SAMPLE bootstrap for a
    # growth rate. Cheap: min/max on an indexed ts, never a count(*).
    spans: dict[str, float | None] = {}
    for n in names:
        has_ts = await con.fetchval(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name=$1 AND column_name='ts'""", n)
        if not has_ts:
            spans[n] = None
            continue
        try:
            r = await con.fetchrow(f'SELECT min(ts) lo, max(ts) hi FROM "{n}"')
            spans[n] = (((r["hi"] - r["lo"]).total_seconds() / 86400.0)
                        if r and r["lo"] and r["hi"] else None)
        except Exception as exc:                      # noqa: BLE001
            log.warning("span probe failed for %s: %r", n, exc)
            spans[n] = None
    return {"db_size": db_size, "top": top, "sizes": sizes,
            "names": sorted(names), "spans": spans}


def collectors_payload(st: dict, prev: dict | None, prev_age_days: float | None) -> dict:
    """Per-table rows/GB plus a growth rate, LABELLED with its estimator.

    'prev_sample' is the real thing: the delta against the previous run.
    'lifetime_span' is the bootstrap for the first run — total size over the
    table's whole history, so it understates a rate that has recently gone up
    and overstates one that has stopped. Never silently mixed with the former.
    """
    out = {}
    for n in st["names"]:
        b, rows = st["sizes"][n]
        gb = b / GIB
        rec = {"rows": rows, "gb": round(gb, 4),
               "rows_per_day": None, "gb_per_day": None, "basis": "none"}
        p = (prev or {}).get(n)
        if p and prev_age_days and prev_age_days > 1.0 / 24.0:
            d_gb = gb - (p.get("gb") or 0.0)
            rec["gb_per_day"] = round(d_gb / prev_age_days, 4)
            if rows is not None and p.get("rows") is not None:
                rec["rows_per_day"] = int((rows - p["rows"]) / prev_age_days)
            rec["basis"] = "prev_sample"
        elif st["spans"].get(n):
            span = st["spans"][n]
            rec["gb_per_day"] = round(gb / span, 4)
            if rows is not None:
                rec["rows_per_day"] = int(rows / span)
            rec["basis"] = "lifetime_span"
        out[n] = rec
    return out


async def table_exists(con, name: str) -> bool:
    """History reads must survive a missing disk_health: that is the state on
    the very first run and under --dry-run, and neither is an error."""
    return bool(await con.fetchval("SELECT to_regclass($1)", f"public.{name}"))


# ── projection ──────────────────────────────────────────────────────────────
async def project(con, mount: str, free_gb: float, collectors: dict) -> dict:
    """days_to_full from the free-space slope, with a labelled fallback."""
    hist = (await con.fetch(
        """SELECT ts, free_gb FROM disk_health
           WHERE mount=$1 AND free_gb IS NOT NULL
           ORDER BY ts DESC LIMIT $2""", mount, SLOPE_SAMPLES)
        if await table_exists(con, "disk_health") else [])
    pts = list(reversed([(r["ts"], r["free_gb"]) for r in hist]))
    if len(pts) >= 3:
        span_h = (pts[-1][0] - pts[0][0]).total_seconds() / 3600.0
        if span_h >= SLOPE_MIN_SPAN_HOURS:
            t0 = pts[0][0]
            xs = [(t - t0).total_seconds() / 86400.0 for t, _ in pts]
            ys = [y for _, y in pts]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
                burn = -slope                      # GiB/day of free space lost
                return {"free_gb_per_day": round(burn, 4),
                        "days_to_full": (round(free_gb / burn, 1) if burn > 1e-6
                                         else None),
                        "basis": "free_slope_ols", "samples": len(pts)}
    # Bootstrap: DB growth only. It EXCLUDES logs, journald and the OS, so it
    # is an optimistic floor on the burn rate, not an estimate of it.
    burn = sum(v["gb_per_day"] or 0.0 for v in collectors.values())
    if burn > 1e-6:
        return {"free_gb_per_day": round(burn, 4),
                "days_to_full": round(free_gb / burn, 1),
                "basis": "collector_gb_per_day(db-only,optimistic)",
                "samples": len(pts)}
    return {"free_gb_per_day": None, "days_to_full": None,
            "basis": "insufficient_history", "samples": len(pts)}


def classify(mount: str, free_pct: float | None, days: float | None) -> tuple[str, str]:
    msgs, status = [], "ok"
    if free_pct is not None and free_pct < CRIT_FREE_PCT:
        status = "critical"
        msgs.append(f"free {free_pct:.1f}% < {CRIT_FREE_PCT:.0f}%")
    elif free_pct is not None and free_pct < WARN_FREE_PCT:
        status = "warning"
        msgs.append(f"free {free_pct:.1f}% < {WARN_FREE_PCT:.0f}%")
    if days is not None and days < CRIT_DAYS:
        status = "critical"
        msgs.append(f"days_to_full {days:.1f} < {CRIT_DAYS:.0f}")
    elif days is not None and days < WARN_DAYS and status != "critical":
        status = "warning"
        msgs.append(f"days_to_full {days:.1f} < {WARN_DAYS:.0f}")
    return status, "; ".join(msgs)


# ── run ─────────────────────────────────────────────────────────────────────
async def run(dry: bool = False) -> int:
    if not DSN:
        raise RuntimeError("NEON_DATABASE_URL not set (check researcher/.env)")
    con = await asyncpg.connect(DSN, command_timeout=120.0, statement_cache_size=0)
    try:
        if not dry:
            await con.execute(CREATE_SQL)

        mps = mounts()
        if not mps:
            raise RuntimeError("no real block-device mounts found")
        vgn, vgf = vg_free()
        st = await db_stats(con)
        dbm = db_mount([m["mount"] for m in mps])

        prev = prev_age = None
        try:
            r = (await con.fetchrow(
                """SELECT ts, collectors FROM disk_health
                   WHERE collectors IS NOT NULL ORDER BY ts DESC LIMIT 1""")
                 if await table_exists(con, "disk_health") else None)
            if r:
                prev = {k: v for k, v in (json.loads(r["collectors"])).items()}
                prev_age = ((datetime.now(timezone.utc) - r["ts"]).total_seconds()
                            / 86400.0)
        except Exception as exc:                      # noqa: BLE001
            log.warning("previous-sample lookup failed: %r", exc)

        cols = collectors_payload(st, prev, prev_age)
        worst = "ok"
        for m in mps:
            is_db = (m["mount"] == dbm)
            pr = await project(con, m["mount"], m["free_gb"], cols if is_db else {})
            status, alerts = classify(m["mount"], m["free_pct"], pr["days_to_full"])
            if status == "critical" or (status == "warning" and worst == "ok"):
                worst = status
            line = (f"[diskwatch] {m['mount']} ({m['device']}): "
                    f"free {m['free_gb']:.1f} GiB of {m['size_gb']:.1f} "
                    f"({m['free_pct']:.1f}% free, {m['used_pct']:.1f}% used) | "
                    f"vg_free {'%.1f GiB' % vgf if vgf is not None else 'UNKNOWN'} | "
                    f"burn {pr['free_gb_per_day']} GiB/day | days_to_full "
                    f"{pr['days_to_full']} [{pr['basis']}, n={pr['samples']}]"
                    + (f" | db {st['db_size'] / GIB:.2f} GiB" if is_db else ""))
            if status == "critical":
                log.critical("%s  ** CRITICAL: %s **", line, alerts)
            elif status == "warning":
                log.warning("%s  ** WARNING: %s **", line, alerts)
            else:
                log.info(line)

            if dry:
                continue
            await con.execute(
                INSERT_SQL, m["mount"], m["device"], m["size_gb"], m["used_gb"],
                m["free_gb"], m["used_pct"], m["free_pct"], vgn, vgf, is_db,
                (st["db_size"] / GIB) if is_db else None,
                json.dumps(st["top"]) if is_db else None,
                json.dumps(cols) if is_db else None,
                pr["free_gb_per_day"], pr["days_to_full"], pr["basis"],
                pr["samples"], status, alerts or None)

        if worst == "ok":
            log.info("[diskwatch] all mounts ok (warn: free<%.0f%% or days<%.0f; "
                     "crit: free<%.0f%% or days<%.0f)",
                     WARN_FREE_PCT, WARN_DAYS, CRIT_FREE_PCT, CRIT_DAYS)
        return 0
    finally:
        await con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="log everything, write nothing")
    a = ap.parse_args()
    try:
        sys.exit(asyncio.run(run(dry=a.dry_run)))
    except SystemExit:
        raise
    except Exception:
        # Loud AND non-zero: a monitor that dies quietly is the failure mode it
        # exists to prevent. systemd marks the unit failed and list-timers stops
        # looking healthy.
        log.exception("[diskwatch] RUN FAILED — exiting non-zero")
        sys.exit(1)


if __name__ == "__main__":
    main()
