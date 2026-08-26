#!/usr/bin/env bash
# Nightly incremental backup of the trading_bot raw series.
#
# Backs up one CLOSED UTC day: [DAY 00:00Z, DAY+1 00:00Z). Closed is the whole
# point — an open window could still be written to, so the artifact would not be
# reproducible and its recorded checksum would be meaningless.
#
#   usage: backup-incremental.sh [YYYY-MM-DD]     (default: yesterday UTC)
#
# Safe to run twice: an artifact already present whose sha256 matches the
# manifest and whose row count still agrees with the database is reused.
# Every artifact is written to .tmp and renamed only after a successful write,
# so a truncated file can never be mistaken for a good one.

LOGTAG=backup-incr
. "$(dirname "$(readlink -f "$0")")/lib.sh"

DAY="${1:-$(date -u -d 'yesterday' +%Y-%m-%d)}"
[[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "bad day '$DAY', want YYYY-MM-DD"
WS="${DAY} 00:00:00+00"
WE="$(date -u -d "$DAY + 1 day" +%Y-%m-%d) 00:00:00+00"
(( $(date -u -d "$WE" +%s) <= $(date -u +%s) )) \
  || die "window ends $WE, which is in the future — refusing to back up an open day"

DSN_V="$(dsn)"
OUTDIR="$BACKUP_ROOT/incr/$DAY"
MANIFEST="$BACKUP_ROOT/manifests/incr-$DAY.json"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
ENTRIES="$WORK/entries.jsonl"; : > "$ENTRIES"
: > "$WORK/failed"
mkdir -p "$OUTDIR" "$BACKUP_ROOT/manifests"

log "incremental backup for UTC day $DAY  window [$WS, $WE)"
check_disk

RC=0
check_unclassified || { warn "run will exit non-zero because of unclassified tables"; RC=3; }

# The schema travels with every night's set, so an increment can be restored
# without hunting down the matching weekly full.
SCHEMA_OUT="$OUTDIR/_schema.sql.zst"
if [[ ! -s "$SCHEMA_OUT" ]]; then
  log "dumping schema"
  pg_dump "$DSN_V" -s --no-owner --no-privileges | zstd -"$ZSTD_LEVEL" -q -f -o "$SCHEMA_OUT.tmp" \
    || die "schema dump failed"
  atomic_finish "$SCHEMA_OUT" "$SCHEMA_OUT.tmp"
fi

record_fail() { printf '%s\n' "$1" >> "$WORK/failed"; }

dump_one() {
  local t=$1 mode=$2 where=$3
  local out="$OUTDIR/${t}.${mode}.zst" tmp="$OUTDIR/${t}.${mode}.zst.tmp"

  if ! table_exists "$t"; then
    warn "table $t is configured for backup but does not exist — NOT silently skipped"
    record_fail "$t:missing"; return 1
  fi
  if [[ "$mode" == incr ]]; then assert_ts_column "$t"; fi

  local rows cols
  rows=$(psql "$DSN_V" -X -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $t $where") \
    || { warn "count failed for $t"; record_fail "$t:count"; return 1; }
  cols=$(psql "$DSN_V" -X -At -v ON_ERROR_STOP=1 -c \
    "SELECT string_agg(attname,',' ORDER BY attnum) FROM pg_attribute
     WHERE attrelid='public.$t'::regclass AND attnum>0 AND NOT attisdropped") \
    || { warn "column list failed for $t"; record_fail "$t:cols"; return 1; }

  # --- idempotence: reuse an artifact that is already provably correct ---
  if [[ -s "$out" && -s "$MANIFEST" ]]; then
    local have_sha
    have_sha=$(sha256sum "$out" | cut -d' ' -f1)
    if python3 - "$MANIFEST" "$t" "$rows" "$have_sha" <<'PY'
import json, sys
manifest, table, rows, sha = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
try:
    doc = json.load(open(manifest))
except Exception:
    sys.exit(1)
e = next((x for x in doc.get("tables", []) if x["table"] == table), None)
sys.exit(0 if e and e["rows"] == rows and e["sha256"] == sha else 1)
PY
    then
      log "  $t [$mode] unchanged (rows=$rows, sha matches manifest) — reusing"
      python3 - "$MANIFEST" "$t" >> "$ENTRIES" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
e = next(x for x in doc["tables"] if x["table"] == sys.argv[2])
e["reused"] = True
print(json.dumps(e, sort_keys=True))
PY
      return 0
    fi
  fi

  local t0 t1 bytes sha secs
  t0=$(date -u +%s%N)
  rm -f "$tmp"
  if ! psql "$DSN_V" -X -q -v ON_ERROR_STOP=1 \
        -c "COPY (SELECT * FROM $t $where) TO STDOUT" \
      | zstd -"$ZSTD_LEVEL" -T0 -q -f -o "$tmp"; then
    warn "COPY/zstd failed for $t"
    rm -f "$tmp"; record_fail "$t:copy"; return 1
  fi
  t1=$(date -u +%s%N)
  secs=$(( (t1 - t0) / 1000000 ))

  atomic_finish "$out" "$tmp"
  bytes=$(stat -c%s "$out")
  sha=$(sha256sum "$out" | cut -d' ' -f1)

  TBL="$t" MODE="$mode" FILE="$(basename "$out")" ROWS="$rows" BYTES="$bytes" \
  SHA="$sha" COLS="$cols" MS="$secs" WS="$WS" WE="$WE" \
  python3 -c '
import json, os
m = os.environ["MODE"]
print(json.dumps({
  "table": os.environ["TBL"], "mode": m, "file": os.environ["FILE"],
  "window_start": os.environ["WS"] if m == "incr" else None,
  "window_end":   os.environ["WE"] if m == "incr" else None,
  "rows": int(os.environ["ROWS"]), "bytes": int(os.environ["BYTES"]),
  "sha256": os.environ["SHA"], "columns": os.environ["COLS"].split(","),
  "seconds": round(int(os.environ["MS"]) / 1000.0, 2), "reused": False,
}, sort_keys=True))' >> "$ENTRIES"

  log "  $t [$mode] rows=$rows bytes=$bytes sha=${sha:0:12} ${secs}ms"
  return 0
}

for t in "${INCR_TABLES[@]}"; do dump_one "$t" incr "WHERE ts >= '$WS' AND ts < '$WE'" || RC=1; done
for t in "${SNAP_TABLES[@]}"; do dump_one "$t" snap "" || RC=1; done

# ---- manifest: lets a restore be verified without reading the data itself ----
DAY="$DAY" WS="$WS" WE="$WE" \
CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)" HOSTN="$(hostname)" \
PGV="$(psql "$DSN_V" -X -At -c 'SHOW server_version')" \
GITSHA="$(git -C "$(dirname "$(readlink -f "$0")")" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
TOTB="$(du -sb "$OUTDIR" | cut -f1)" \
python3 - "$MANIFEST" "$ENTRIES" "$WORK/failed" <<'PY'
import json, os, sys
manifest, entries_path, failed_path = sys.argv[1:4]
entries = [json.loads(l) for l in open(entries_path) if l.strip()]
failed  = [l.strip() for l in open(failed_path) if l.strip()]
doc = {
    "kind": "incremental", "day": os.environ["DAY"],
    "window_start": os.environ["WS"], "window_end": os.environ["WE"],
    "created_at": os.environ["CREATED"], "host": os.environ["HOSTN"],
    "database": "trading_bot", "pg_version": os.environ["PGV"],
    "generator": "ops/backup/backup-incremental.sh", "git_sha": os.environ["GITSHA"],
    "total_bytes": int(os.environ["TOTB"]), "failed": failed,
    "shipped": False, "tables": entries,
}
tmp = manifest + ".tmp"
json.dump(doc, open(tmp, "w"), indent=2, sort_keys=True)
os.replace(tmp, manifest)
print("manifest: %s — %d tables, %d rows, %.1f MB, %d failed"
      % (manifest, len(entries), sum(e["rows"] for e in entries),
         int(os.environ["TOTB"]) / 1e6, len(failed)))
PY

if [[ -s "$WORK/failed" ]]; then
  warn "FAILED TABLES: $(tr '\n' ' ' < "$WORK/failed")"
  RC=1
fi

# ---- ship off the box, then prune local ----
if ! "$(dirname "$(readlink -f "$0")")/ship.sh" incr "$DAY"; then
  [[ "$SHIP_REQUIRED" == "1" ]] && die "shipping failed and SHIP_REQUIRED=1"
  warn "NOT SHIPPED — a local-only backup does not protect against this disk dying"
fi

if [[ "$SHIP_ENABLED" == "1" ]]; then
  find "$BACKUP_ROOT/incr" -mindepth 1 -maxdepth 1 -type d -mtime "+$LOCAL_KEEP_INCR_DAYS" \
    -printf 'pruning local %p\n' -exec rm -rf {} + >&2 || true
fi

log "done rc=$RC"
exit $RC
