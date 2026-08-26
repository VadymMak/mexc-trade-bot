#!/usr/bin/env bash
# Prove the restore path, not just the backup path.
#
#   usage: restore-verify.sh [YYYY-MM-DD] [table]
#          default: yesterday, venue_funding_snapshots
#
# Restores ONE table's increment into a throwaway database and compares, against
# the live source over the same window:
#     - row count
#     - md5 of the whole windowed relation, rendered row-by-row in id order
# A backup that has never been restored is a hypothesis, not a backup.
#
# Touches nothing in trading_bot: reads only, and all writes go to a scratch
# database that is dropped at the end.

LOGTAG=restore-verify
. "$(dirname "$(readlink -f "$0")")/lib.sh"

DAY="${1:-$(date -u -d 'yesterday' +%Y-%m-%d)}"
TABLE="${2:-venue_funding_snapshots}"
SCRATCH="${SCRATCH_DB:-mexc_restore_test}"

DSN_V="$(dsn)"
SRCDIR="$BACKUP_ROOT/incr/$DAY"
ART="$SRCDIR/${TABLE}.incr.zst"
[[ -s "$ART" ]] || ART="$SRCDIR/${TABLE}.snap.zst"
[[ -s "$ART" ]] || die "no artifact for $TABLE on $DAY under $SRCDIR"
[[ -s "$SRCDIR/_schema.sql.zst" ]] || die "no schema dump in $SRCDIR"

MANIFEST="$BACKUP_ROOT/manifests/incr-$DAY.json"
[[ -s "$MANIFEST" ]] || die "no manifest $MANIFEST"

WS="${DAY} 00:00:00+00"
WE="$(date -u -d "$DAY + 1 day" +%Y-%m-%d) 00:00:00+00"
MODE=$([[ "$ART" == *.incr.zst ]] && echo incr || echo snap)
WHERE=$([[ "$MODE" == incr ]] && echo "WHERE ts >= '$WS' AND ts < '$WE'" || echo "")

log "verifying restore of $TABLE [$MODE] from $DAY"
log "artifact: $ART ($(stat -c%s "$ART") bytes)"

# --- 0. the artifact on disk must still match what the manifest recorded ---
DISK_SHA=$(sha256sum "$ART" | cut -d' ' -f1)
MAN_SHA=$(python3 -c "
import json,sys
d=json.load(open('$MANIFEST'))
e=next((x for x in d['tables'] if x['table']=='$TABLE'),None)
print(e['sha256'] if e else '')")
MAN_ROWS=$(python3 -c "
import json
d=json.load(open('$MANIFEST'))
e=next((x for x in d['tables'] if x['table']=='$TABLE'),None)
print(e['rows'] if e else -1)")
[[ "$DISK_SHA" == "$MAN_SHA" ]] \
  || die "artifact sha256 does not match manifest (disk=$DISK_SHA manifest=$MAN_SHA)"
log "artifact sha256 matches manifest: ${DISK_SHA:0:12}"

# --- 1. source-side truth, computed from the live database ---
log "computing source checksum over [$WS, $WE)"
read -r SRC_ROWS SRC_MD5 <<<"$(psql "$DSN_V" -X -At -F' ' -v ON_ERROR_STOP=1 -c "
  SELECT count(*), coalesce(md5(string_agg(x, E'\n')), 'empty')
  FROM (SELECT t::text AS x FROM $TABLE t $WHERE ORDER BY id) s")"
log "source: rows=$SRC_ROWS md5=$SRC_MD5"

# --- 2. rebuild the table in a scratch database and load the artifact ---
PSQL_SU=(sudo -u postgres psql -X -v ON_ERROR_STOP=1 -q)
log "creating scratch database $SCRATCH"
"${PSQL_SU[@]}" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null
"${PSQL_SU[@]}" -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
cleanup() {
  sudo -u postgres psql -X -q -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Load the whole schema (no data), then keep only what we need.
log "loading schema into $SCRATCH"
zstd -dc "$SRCDIR/_schema.sql.zst" | sudo -u postgres psql -X -q -d "$SCRATCH" \
  -v ON_ERROR_STOP=0 >/dev/null 2>&1

COLS=$(python3 -c "
import json
d=json.load(open('$MANIFEST'))
e=next(x for x in d['tables'] if x['table']=='$TABLE')
print(','.join(e['columns']))")

log "restoring $TABLE via COPY FROM"
zstd -dc "$ART" | sudo -u postgres psql -X -q -d "$SCRATCH" -v ON_ERROR_STOP=1 \
  -c "COPY $TABLE ($COLS) FROM STDIN" || die "COPY FROM failed — the artifact did not restore"

# --- 3. compare ---
read -r DST_ROWS DST_MD5 <<<"$(sudo -u postgres psql -X -At -F' ' -q -d "$SCRATCH" -c "
  SELECT count(*), coalesce(md5(string_agg(x, E'\n')), 'empty')
  FROM (SELECT t::text AS x FROM $TABLE t ORDER BY id) s")"
log "restored: rows=$DST_ROWS md5=$DST_MD5"

RC=0
[[ "$SRC_ROWS" == "$DST_ROWS" ]]   || { warn "ROW COUNT MISMATCH src=$SRC_ROWS dst=$DST_ROWS"; RC=1; }
[[ "$MAN_ROWS" == "$DST_ROWS" ]]   || { warn "MANIFEST ROW MISMATCH manifest=$MAN_ROWS dst=$DST_ROWS"; RC=1; }
[[ "$SRC_MD5"  == "$DST_MD5"  ]]   || { warn "CHECKSUM MISMATCH src=$SRC_MD5 dst=$DST_MD5"; RC=1; }

if (( RC == 0 )); then
  log "RESTORE VERIFIED: $TABLE $DAY — $DST_ROWS rows, md5 $DST_MD5, source and restore identical"
else
  warn "RESTORE VERIFICATION FAILED for $TABLE $DAY"
fi
exit $RC
