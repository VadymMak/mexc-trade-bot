# Server & Project Status Report — mexc-trade-bot

> Historical as of 2026-08-26 — superseded by BLUEPRINT.md.

> Generated: **2026-08-13** · Host: `trading-server` · Verified live, not from docs

Handoff document for another agent/session. Everything below was checked against
the running system (systemd, `ss`, PostgreSQL), not inferred from the repo.

---

## 1. Answer: what kind of deployment is this?

**A local Ubuntu server at home ("trading-server"). Not Railway, not a cloud VPS.**

```
Hostname:     trading-server
OS:           Ubuntu 26.04 LTS, kernel 7.0.0-29-generic, x86-64
Chassis:      desktop
Uptime:       6 days
Disk:         98 G total, 41 G used (45 %), 52 G free
Remote access: Tailscale (100.112.227.114) + SSH
Process mgr:  systemd
Proxy:        NONE — no nginx / caddy / traefik anywhere
Containers:   NONE — Docker files exist but are not used
```

Migration history: the project started on **Railway**, and
`VPS_MIGRATION_PLAN.md` proposed moving to a **Hetzner CX22**. Neither is in
force — the workload was moved to **this home server** instead. The Hetzner
plan's 10 checkboxes are all still unchecked and it should be treated as
abandoned.

**Legacy deploy artifacts that must be ignored** (they describe environments
that no longer exist):

| File | Describes | Reality |
|------|-----------|---------|
| `backend/docker-compose.yml` | Docker + SQLite `DB_PATH=/app/data/mexc.db` | unused; also wrong DB |
| `backend/Dockerfile` | container build | unused |
| `ecosystem.config.js` | PM2, user `bot`, `/home/bot/...` | unused; systemd + `/home/vadym/...` |
| `researcher/Procfile` | Railway worker | unused |
| `railpack-plan.json` | Railway | empty `{}` |
| `vps-setup.sh` | Hetzner bootstrap | never run |

---

## 2. Running services (systemd)

| Unit | Port | Command | State |
|------|------|---------|-------|
| `mexc-backend` | **127.0.0.1:8000** | `backend/.venv/bin/python -m uvicorn app.main:app` | ✅ active — canonical backend |
| `mexc-carry-collector` | — | `researcher/.venv/bin/python -m app.carry.main` | ✅ active, writing data |
| `mexc-frontend` | **0.0.0.0:3000** | `npm run start -- -H 0.0.0.0 -p 3000` | ✅ active |
| `trading-bot` | — | duplicate backend, was on 0.0.0.0:8000 | ⛔ **stopped + disabled** (2026-08-13) |
| `mexc-researcher` | 8100 / 8101 | `researcher/.venv/bin/python -m app.main` | ⛔ **disabled — not running** |

Listening sockets confirmed via `ss -tlnp`:

```
127.0.0.1:8000  python -m uvicorn (mexc-backend)  ← single listener
0.0.0.0:3000    next-server
127.0.0.1:5432  postgres
```

Ports 8100/8101 are **not** listening — the paper/live trader is stopped.

### Backend consolidation (2026-08-13)

`trading-bot.service` and `mexc-backend.service` ran the same app on port 8000.
Both used the **same** `WorkingDirectory` (`/home/vadym/mexc-trade-bot/backend`)
and the **same** venv, so they were functionally equivalent — the app loads
`backend/.env` itself in `app/config/settings.py` (`load_dotenv`), which is why
`trading-bot` worked despite having **no** `EnvironmentFile=`.

| | `trading-bot` (removed) | `mexc-backend` (kept) |
|---|---|---|
| ExecStart | `.venv/bin/uvicorn` | `.venv/bin/python -m uvicorn` |
| Bind | `0.0.0.0:8000` | `127.0.0.1:8000` |
| EnvironmentFile | none | `backend/.env` (explicit) |
| Restart | always / 10 s | always / 5 s |

`mexc-backend` was kept: explicit env file and a **private loopback bind** (the
frontend proxies via `127.0.0.1:8000`, so nothing else needs the LAN binding).
`trading-bot` was stopped and disabled; its 232-restart crash-loop is gone and
`mexc-backend` now holds the port with `NRestarts=0`.

**Note:** the unit files live in `/etc/systemd/system/` and are **not tracked in
this repo**, so this change has no corresponding code commit.

---

## 3. Database

**Single local PostgreSQL: `postgresql://mexc@127.0.0.1:5432/trading_bot`**
(loopback only, size ~1.26 GB). SQLite is fully retired.

Credentials live in `/home/vadym/mexc-db-credentials.env` and in each service's
`.env`. Note the **misleading variable names** — all of these point at the same
local database:

| Service | Variable(s) used |
|---------|------------------|
| backend | `DATABASE_URL`, `ML_DATABASE_URL`, `NEON_DATABASE_URL` |
| researcher | `NEON_DATABASE_URL` ← historical name, **not** Neon |
| ml_collector | `DATABASE_URL` (new, as of this commit) |

⚠️ `researcher/.env.example` still contains a **real Neon hostname**
(`ep-tiny-sky-anbfr314...neon.tech`) and Railway URLs — misleading for anyone
setting up a fresh copy.

### Table state

| Table | Rows | Latest row | Status |
|-------|------|-----------|--------|
| `funding_basis_snapshots` | 5,804,801 | live (≤5 min) | ✅ **growing** — 1,193 rows / 5 min, 727 coins × 2 exchanges (gate 529 + mexc 680) |
| `spread_observations` | 471,481 | 2026-07-27 | frozen |
| `paper_positions` | 1,900 | 2026-07-25 | frozen |
| `ml_trade_outcomes` | 1,888 | 2026-07-25 | frozen (arb archive intact) |
| `ml_snapshots` | seeding | live-capable | now wired to ml_collector |
| `trades` | 0 | — | empty |

**Only the carry collector produces new data.** All researcher-derived datasets
have been static since 2026-07-27.

---

## 4. Changes made in this session (commit `0c66825`)

### ml_collector → PostgreSQL

The collector was still fully SQLite-bound and — the important part — **never
read its `.env` at all**: `config.py` hardcoded
`DB_PATH = ../backend/mexc.db` as a `Path` literal, and that file does not even
exist any more. So `ml_snapshots` had **0 rows**; the collector had been dead
for a long time.

Fixed:

- `config.py` — loads `.env` via `dotenv`, **requires `DATABASE_URL`** (raises
  with a clear message if missing), `DB_PATH` removed entirely. Symbols,
  interval, scanner URL and log level now come from env with sane defaults.
- `collector.py` — `sqlite3` → `psycopg2`; `?` → `%s` placeholders; existence
  check via `information_schema` instead of `sqlite_master`; one persistent
  autocommit connection that reconnects after a failed write instead of
  open/close per row; password masked in log output.
- `.env` — `DB_PATH` removed, `DATABASE_URL` added (old file kept as
  `.env.sqlite.bak`, gitignored).
- `requirements.txt` — added `psycopg2-binary`, `aiohttp` (aiohttp was imported
  but never declared).

**Bonus bug found while verifying:** three columns were silently writing zeros
because the field names didn't match the scanner payload — the API returns
`trades_per_min` / `usd_per_min` (code read `tpm` / `usdpm`) and has **no
`mid` field at all** (now derived from bid/ask). Any historical `ml_snapshots`
data would have had `mid`, `trades_per_min` and `usd_per_min` all zero.

**Verified end-to-end:** ran the collector against the live backend; rows landed
in Postgres with every column populated
(`VETUSDT bid=0.00449 ask=0.004493 mid=0.0044915 spread_bps=6.68
trades_per_min=1 usd_per_min=9.33`).

### PROJECT_STATUS.md

Was 9 months stale (Nov 13 2025, Railway + SQLite + "Dataset v2 18/5000
trades"). Rewritten with the current architecture at the top; the old Phase 2
narrative kept below under a **HISTORICAL ARCHIVE** header rather than deleted.

---

## 5. Open issues, in priority order

1. ✅ **RESOLVED 2026-08-13 — duplicate backend service.** `trading-bot.service`
   stopped and disabled; `mexc-backend.service` is the single backend, bound to
   `127.0.0.1:8000`. See "Backend consolidation" above.
2. ⚠️ **`/api/carry/export-dataset` works but is a memory bomb.** It returns
   **HTTP 200**, but the handler does `fetchall()` over the entire
   `funding_basis_snapshots` table (5.8 M rows) before streaming: headers arrive
   only after **~29 s**, the backend balloons to **~4.8 GB RSS**, and the body
   exceeded **813 MB** without finishing in 5 minutes. It should use a
   server-side cursor / `yield_per` and accept a time-range filter. Do not
   casually smoke-test this endpoint.
3. **ml_collector has no systemd unit.** It must be started by hand and will not
   survive a reboot. If continuous `ml_snapshots` collection is wanted, it needs
   a unit like the other four.
4. **Researcher is stopped** (`mexc-researcher` disabled since ~2026-07-27).
   This is presumably deliberate — executable book-walked P&L showed 0 % win
   rate / −127 bps, invalidating the earlier 95.7 % mark-price figures — but it
   means no new paper-trading data is being produced.
5. **`NEON_DATABASE_URL` naming.** Points at local Postgres everywhere. The name
   plus the real Neon host still in `researcher/.env.example` is a trap.
6. **Stale SQLite analysis scripts** in `ml_collector/`
   (`analyze_trades.py`, `analyze_mm_patterns.py`, `check_db_structure.py`,
   `trade_tracker.py`) still open `../backend/mexc.db`, which no longer exists.
   They will fail if run.
7. **Dead Windows venv:** `ml_collector/.venv` contains `Lib/` + `Scripts/`
   (Windows layout) with no `bin/python`. The collector currently borrows
   `backend/.venv`, which does have `psycopg2`, `aiohttp` and `dotenv`.

---

## 6. Useful commands

```bash
# service state
systemctl is-active mexc-researcher mexc-backend mexc-frontend \
                    mexc-carry-collector trading-bot

# database
DSN=$(sed -n 's/^DATABASE_URL=//p' /home/vadym/mexc-db-credentials.env | tr -d '"')
psql "$DSN" -c "SELECT count(*), max(ts) FROM funding_basis_snapshots;"

# run the ml collector (manual, needs backend up on :8000)
cd /home/vadym/mexc-trade-bot/ml_collector
../backend/.venv/bin/python collector.py
```
