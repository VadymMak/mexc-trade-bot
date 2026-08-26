#!/usr/bin/env bash
# Shared configuration and helpers for the trading_bot PostgreSQL backups.
# Sourced by backup-incremental.sh / backup-full.sh / ship.sh / restore-verify.sh.
#
# Why this exists: as of 2026-08-26 the ~21 GB of collected series existed on
# exactly one LV on one NVMe, in a house whose breaker had tripped twice in two
# days. Corruption is not the risk here (data_checksums=on, 0 failures ever) —
# the single copy is.

set -euo pipefail

# ---------------------------------------------------------------- defaults ---
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mexc}"
CRED_FILE="${CRED_FILE:-/home/vadym/mexc-db-credentials.env}"
ZSTD_LEVEL="${ZSTD_LEVEL:-3}"
MIN_FREE_GB="${MIN_FREE_GB:-50}"

# Tier 1 — the Mac over Tailscale. Off this box, immune to the house breaker.
SHIP_ENABLED="${SHIP_ENABLED:-0}"      # flip to 1 once the key is installed
SHIP_REQUIRED="${SHIP_REQUIRED:-0}"    # 1 => failure to ship is a hard error
MAC_HOST="${MAC_HOST:-100.73.144.101}"
MAC_USER="${MAC_USER:-vadym}"
MAC_PATH="${MAC_PATH:-mexc-backups}"
SSH_KEY="${SSH_KEY:-/root/.ssh/id_ed25519_mexcbackup}"

# Tier 2 — offsite object store. STUB: set OFFSITE_ENABLED=1 and an rclone
# remote in /etc/mexc-backup.conf once a bucket exists. ~180 GB/yr ~= $1/month.
OFFSITE_ENABLED="${OFFSITE_ENABLED:-0}"
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"   # e.g. "b2:mexc-trade-bot-backups"

# Retention. Remote keeps everything; local keeps only a shipping buffer, so we
# never hold two copies of the dataset on the one disk we are protecting.
LOCAL_KEEP_INCR_DAYS="${LOCAL_KEEP_INCR_DAYS:-3}"
LOCAL_KEEP_FULLS="${LOCAL_KEEP_FULLS:-1}"
REMOTE_KEEP_FULLS="${REMOTE_KEEP_FULLS:-4}"

[[ -r /etc/mexc-backup.conf ]] && . /etc/mexc-backup.conf

# ------------------------------------------------------------ table lists ---
# INCR: append-only and keyed on a `ts` column, so a closed-day window
# [00:00Z, 00:00Z+1d) is stable and can never be rewritten behind us.
INCR_TABLES=(
  carry_book_l2
  ersh_book_l2
  funding_basis_snapshots
  venue_funding_snapshots
  tape_prints
  book_ticker
  basis_snapshots
  lending_snapshots
  lp_snapshots
  spread_observations
)

# SNAP: dumped in full every night, not windowed. Two reasons a table lands
# here, both fatal to a ts-windowed incremental:
#   - it has no `ts` column at all (paper_carry_positions keys on opened_ts /
#     closed_ts; carry_funding_intervals, symbol_states, pair_stats,
#     carry_reentry_blocks have none), or
#   - its rows are UPDATEd after insert, so a row written on day N can change
#     on day N+5 and a day-N window would never see the new value
#     (pg_stat_user_tables n_tup_upd: carry_funding_intervals 2092,
#     paper_carry_positions 24, paper_carry_events 1).
# All of these are small — the whole SNAP set is well under 100 MB of heap —
# so a nightly full copy is cheaper than being subtly wrong.
SNAP_TABLES=(
  paper_carry_events
  paper_carry_positions
  paper_positions
  carry_funding_intervals
  carry_reentry_blocks
  symbol_states
  pair_stats
  ml_trade_outcomes
  disk_health

  # Legacy arb-bot tables. As of 2026-08-26 all are empty except bot_config
  # (1 row), but "empty today" is not a guarantee and bot_config is
  # configuration that is not reconstructible from anything else. Together they
  # cost about 10 KB a night, so they are snapshotted rather than argued about.
  bot_config
  fills
  orders
  pnl_daily
  pnl_ledger
  positions
  scalp_positions
  sessions
  spread_ticks
  strategy_state
  trades
)

# REBUILDABLE: deliberately not in the nightly. These are derived from the
# tables above or are operational telemetry; they still ride along in the
# weekly full, which dumps the entire database.
REBUILDABLE_TABLES=(
  carry_collector_health
  venue_collector_health
  basis_collector_health
  lending_collector_health
  lp_collector_health
  bybit_collector_health
  bybit_funding_snapshots
  trade_embeddings
  rag_contexts
  ml_snapshots
  ui_state
)

# ---------------------------------------------------------------- helpers ---
log()  { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${LOGTAG:-backup}" "$*" >&2; }
warn() { printf '%s [%s] WARN: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${LOGTAG:-backup}" "$*" >&2; }
die()  { printf '%s [%s] FATAL: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${LOGTAG:-backup}" "$*" >&2; exit 1; }

dsn() {
  [[ -r "$CRED_FILE" ]] || die "cannot read $CRED_FILE (run as root)"
  local d; d=$(sed -n 's/^DATABASE_URL=//p' "$CRED_FILE")
  [[ -n "$d" ]] || die "no DATABASE_URL in $CRED_FILE"
  printf '%s' "$d"
}

# Never let a backup be the thing that fills the disk it is protecting.
check_disk() {
  local free_gb
  free_gb=$(df -BG --output=avail "$BACKUP_ROOT" | tail -1 | tr -dc '0-9')
  (( free_gb >= MIN_FREE_GB )) || die "only ${free_gb}G free under $BACKUP_ROOT, need ${MIN_FREE_GB}G"
  log "disk ok: ${free_gb}G free"
}

# Fail loudly if the database has grown a table nobody has classified — the
# alternative is a new collector's data silently never being backed up.
check_unclassified() {
  local known found unknown=()
  known=$(printf '%s\n' "${INCR_TABLES[@]}" "${SNAP_TABLES[@]}" "${REBUILDABLE_TABLES[@]}" | sort -u)
  found=$(psql "$(dsn)" -At -c \
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1")
  while read -r t; do
    [[ -z "$t" ]] && continue
    grep -qxF "$t" <<<"$known" || unknown+=("$t")
  done <<<"$found"
  if (( ${#unknown[@]} )); then
    warn "UNCLASSIFIED TABLES (not in any backup list): ${unknown[*]}"
    warn "add them to INCR_TABLES or SNAP_TABLES in ops/backup/lib.sh"
    return 1
  fi
  return 0
}

# Assert an INCR table really has the ts column we are about to window on.
assert_ts_column() {
  local t=$1 n
  n=$(psql "$(dsn)" -At -c \
    "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
     JOIN pg_namespace ns ON ns.oid=c.relnamespace
     WHERE ns.nspname='public' AND c.relname='$t' AND a.attname='ts' AND a.attnum>0 AND NOT a.attisdropped")
  [[ "$n" == "1" ]] || die "table $t is in INCR_TABLES but has no ts column"
}

table_exists() {
  local t=$1 n
  n=$(psql "$(dsn)" -At -c "SELECT to_regclass('public.$t') IS NOT NULL")
  [[ "$n" == "t" ]]
}

# Write $2 to $1 atomically: temp name, fsync, rename. A truncated file must
# never be mistaken for a good one.
atomic_finish() {
  local final=$1 tmp=$2
  sync "$tmp"
  mv -f "$tmp" "$final"
  sync "$(dirname "$final")" 2>/dev/null || true
}
