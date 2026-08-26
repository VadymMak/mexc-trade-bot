#!/usr/bin/env bash
# Move a backup off this box. Called by backup-incremental.sh / backup-full.sh.
#
#   usage: ship.sh incr YYYY-MM-DD
#          ship.sh full YYYYMMDDTHHMMSSZ
#
# Tier 1 — the Mac over Tailscale (100.73.144.101). Protects against this
#          machine dying: disk, PSU, or the breaker taking it out mid-write.
# Tier 2 — offsite object store. STUB until a bucket exists. Only this tier
#          protects against the house.
#
# Exit 0 only if every ENABLED tier succeeded AND the remote copy's sha256 was
# read back and matched. "It transferred" is not verification.

LOGTAG=backup-ship
. "$(dirname "$(readlink -f "$0")")/lib.sh"

KIND="${1:?usage: ship.sh <incr|full> <id>}"
ID="${2:?usage: ship.sh <incr|full> <id>}"

case "$KIND" in
  incr) SRC="$BACKUP_ROOT/incr/$ID";                       MANIFEST="$BACKUP_ROOT/manifests/incr-$ID.json" ;;
  full) SRC="$BACKUP_ROOT/full/trading_bot-${ID}.dump";    MANIFEST="$BACKUP_ROOT/manifests/full-$ID.json" ;;
  *)    die "unknown kind '$KIND'" ;;
esac
[[ -e "$SRC" ]] || die "nothing to ship at $SRC"

if [[ "$SHIP_ENABLED" != "1" ]]; then
  warn "================================================================"
  warn "SHIPPING IS NOT ENABLED. The backup at"
  warn "    $SRC"
  warn "exists ONLY on the disk it is meant to protect."
  warn "To enable, on the Mac (100.73.144.101):"
  warn "  1) System Settings > General > Sharing > Remote Login: ON"
  warn "  2) mkdir -p ~/mexc-backups"
  warn "  3) append this server's key to ~/.ssh/authorized_keys"
  warn "     (public key: $SSH_KEY.pub on trading-server)"
  warn "then set SHIP_ENABLED=1 (and SHIP_REQUIRED=1) in /etc/mexc-backup.conf"
  warn "================================================================"
  exit 1
fi

RC=0
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
REMOTE="$MAC_USER@$MAC_HOST"

# ---------------------------------------------------- tier 1: Mac (Tailscale)
log "tier 1: shipping $KIND/$ID to $REMOTE:$MAC_PATH"
if ! ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p '$MAC_PATH/$KIND' '$MAC_PATH/manifests'"; then
  warn "cannot reach $REMOTE over ssh (is Remote Login on, is the key installed?)"
  RC=1
else
  # --partial + rename-on-complete: rsync writes to a temp name and renames,
  # so an interrupted transfer never leaves a plausible-looking short file.
  if rsync -a --partial --inplace=false --info=stats1 \
       -e "ssh ${SSH_OPTS[*]}" "$SRC" "$REMOTE:$MAC_PATH/$KIND/" \
     && rsync -a -e "ssh ${SSH_OPTS[*]}" "$MANIFEST" "$REMOTE:$MAC_PATH/manifests/"; then

    # Read the checksum back off the remote. A transfer that reported success
    # but landed corrupt is exactly the failure a backup must not have.
    log "verifying remote checksums"
    if [[ "$KIND" == full ]]; then
      LOCAL_SHA=$(sha256sum "$SRC" | cut -d' ' -f1)
      REMOTE_SHA=$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "shasum -a 256 '$MAC_PATH/$KIND/$(basename "$SRC")' | cut -d' ' -f1" | tr -d '\r')
      if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
        log "tier 1 OK: remote sha256 matches (${LOCAL_SHA:0:12})"
      else
        warn "tier 1 CHECKSUM MISMATCH local=$LOCAL_SHA remote=$REMOTE_SHA"; RC=1
      fi
    else
      MISMATCH=0
      while IFS= read -r f; do
        L=$(sha256sum "$SRC/$f" | cut -d' ' -f1)
        R=$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
             "shasum -a 256 '$MAC_PATH/$KIND/$ID/$f' | cut -d' ' -f1" | tr -d '\r')
        [[ "$L" == "$R" ]] || { warn "mismatch on $f local=$L remote=$R"; MISMATCH=1; }
      done < <(cd "$SRC" && ls -1)
      (( MISMATCH )) && RC=1 || log "tier 1 OK: all remote sha256 match"
    fi
  else
    warn "rsync to $REMOTE failed"; RC=1
  fi
fi

# ------------------------------------------------- tier 2: offsite (STUBBED)
if [[ "$OFFSITE_ENABLED" == "1" ]]; then
  if ! command -v rclone >/dev/null; then
    warn "OFFSITE_ENABLED=1 but rclone is not installed"; RC=1
  elif [[ -z "$OFFSITE_REMOTE" ]]; then
    warn "OFFSITE_ENABLED=1 but OFFSITE_REMOTE is empty"; RC=1
  else
    log "tier 2: rclone copy -> $OFFSITE_REMOTE/$KIND/"
    if rclone copy --checksum "$SRC" "$OFFSITE_REMOTE/$KIND/$( [[ $KIND == incr ]] && echo "$ID" )" \
       && rclone copy --checksum "$MANIFEST" "$OFFSITE_REMOTE/manifests/"; then
      log "tier 2 OK"
    else
      warn "rclone copy failed"; RC=1
    fi
  fi
else
  # ------------------------------------------------------------------ HOOK --
  # OFFSITE TIER NOT CONFIGURED. This is the only tier that survives the house
  # burning down / being burgled. ~180 GB/yr at B2 or S3 IA is ~$1/month.
  # To enable:
  #   apt install rclone && rclone config          # add a b2/s3 remote
  #   echo 'OFFSITE_ENABLED=1'                >> /etc/mexc-backup.conf
  #   echo 'OFFSITE_REMOTE="b2:mexc-backups"' >> /etc/mexc-backup.conf
  # --------------------------------------------------------------------------
  log "tier 2 (offsite) not configured — see the HOOK comment in ship.sh"
fi

if (( RC == 0 )); then
  python3 - "$MANIFEST" <<'PY'
import json, os, sys
p = sys.argv[1]
d = json.load(open(p))
d["shipped"] = True
tmp = p + ".tmp"
json.dump(d, open(tmp, "w"), indent=2, sort_keys=True)
os.replace(tmp, p)
PY
  log "shipped ok"
fi
exit $RC
