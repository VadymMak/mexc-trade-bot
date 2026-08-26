#!/usr/bin/env bash
# Weekly full backup of trading_bot — the rebase point the nightly increments
# are layered on. Includes everything: raw series, derived tables, views,
# sequences, indexes.
#
#   usage: backup-full.sh [--keep-local]
#
# Measured 2026-08-26: 3.98 GB in 135 s against the live database.

LOGTAG=backup-full
. "$(dirname "$(readlink -f "$0")")/lib.sh"

KEEP_LOCAL=0
[[ "${1:-}" == "--keep-local" ]] && KEEP_LOCAL=1

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DSN_V="$(dsn)"
OUT="$BACKUP_ROOT/full/trading_bot-${STAMP}.dump"
MANIFEST="$BACKUP_ROOT/manifests/full-${STAMP}.json"
mkdir -p "$BACKUP_ROOT/full" "$BACKUP_ROOT/manifests"

log "full backup -> $OUT"
check_disk

RC=0
check_unclassified || { warn "run will exit non-zero because of unclassified tables"; RC=3; }

# Row counts are taken BEFORE the dump. pg_dump runs in a repeatable-read
# snapshot, so the archive holds at least these rows for append-only tables;
# the manifest records both so a restore comparison is interpretable.
log "counting rows (pre-dump floor)"
COUNTS="$(mktemp)"; trap 'rm -f "$COUNTS"' EXIT
for t in "${INCR_TABLES[@]}" "${SNAP_TABLES[@]}" "${REBUILDABLE_TABLES[@]}"; do
  table_exists "$t" || { warn "configured table $t missing"; RC=1; continue; }
  printf '%s\t%s\n' "$t" \
    "$(psql "$DSN_V" -X -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $t")" >> "$COUNTS"
done

T0=$(date -u +%s)
rm -f "$OUT.tmp"
if ! pg_dump "$DSN_V" -Fc -Z "zstd:$ZSTD_LEVEL" --no-owner --no-privileges -f "$OUT.tmp"; then
  warn "pg_dump failed"
  rm -f "$OUT.tmp"
  die "full backup failed for $STAMP"
fi
T1=$(date -u +%s)
atomic_finish "$OUT" "$OUT.tmp"

# An archive that pg_restore cannot even list is not a backup.
log "verifying archive table of contents"
TOC="$(mktemp)"; trap 'rm -f "$COUNTS" "$TOC"' EXIT
pg_restore --list "$OUT" > "$TOC" || die "pg_restore --list failed — archive is unreadable"
for t in "${INCR_TABLES[@]}" "${SNAP_TABLES[@]}"; do
  grep -q "TABLE DATA public $t " "$TOC" || { warn "archive has no TABLE DATA for $t"; RC=1; }
done

BYTES=$(stat -c%s "$OUT")
SHA=$(sha256sum "$OUT" | cut -d' ' -f1)
log "full dump ok: $BYTES bytes in $((T1-T0))s sha=${SHA:0:12}"

STAMP="$STAMP" FILE="$(basename "$OUT")" BYTES="$BYTES" SHA="$SHA" SECS="$((T1-T0))" \
CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)" HOSTN="$(hostname)" \
PGV="$(psql "$DSN_V" -X -At -c 'SHOW server_version')" \
GITSHA="$(git -C "$(dirname "$(readlink -f "$0")")" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
TOCN="$(grep -c 'TABLE DATA' "$TOC")" \
python3 - "$MANIFEST" "$COUNTS" <<'PY'
import json, os, sys
manifest, counts_path = sys.argv[1], sys.argv[2]
tables = []
for line in open(counts_path):
    name, n = line.rstrip("\n").split("\t")
    tables.append({"table": name, "rows_at_dump_start": int(n)})
doc = {
    "kind": "full", "stamp": os.environ["STAMP"], "file": os.environ["FILE"],
    "created_at": os.environ["CREATED"], "host": os.environ["HOSTN"],
    "database": "trading_bot", "pg_version": os.environ["PGV"],
    "generator": "ops/backup/backup-full.sh", "git_sha": os.environ["GITSHA"],
    "format": "pg_dump custom (-Fc -Z zstd:3)",
    "bytes": int(os.environ["BYTES"]), "sha256": os.environ["SHA"],
    "seconds": int(os.environ["SECS"]),
    "table_data_entries": int(os.environ["TOCN"]),
    "shipped": False, "tables": tables,
}
tmp = manifest + ".tmp"
json.dump(doc, open(tmp, "w"), indent=2, sort_keys=True)
os.replace(tmp, manifest)
print("manifest: %s — %d tables, %.2f GB" % (manifest, len(tables), int(os.environ["BYTES"]) / 1e9))
PY

if ! "$(dirname "$(readlink -f "$0")")/ship.sh" full "$STAMP"; then
  [[ "$SHIP_REQUIRED" == "1" ]] && die "shipping failed and SHIP_REQUIRED=1"
  warn "NOT SHIPPED — a local-only backup does not protect against this disk dying"
  KEEP_LOCAL=1
fi

# Ship, then delete. Two copies of the dataset on the one disk we are
# protecting is exactly the thing this whole exercise exists to avoid.
if [[ "$SHIP_ENABLED" == "1" && "$KEEP_LOCAL" == "0" ]]; then
  mapfile -t OLD < <(ls -1t "$BACKUP_ROOT"/full/trading_bot-*.dump 2>/dev/null | tail -n +$((LOCAL_KEEP_FULLS+1)))
  for f in "${OLD[@]:-}"; do [[ -n "$f" ]] && { log "pruning local $f"; rm -f "$f"; }; done
fi

log "done rc=$RC"
exit $RC
