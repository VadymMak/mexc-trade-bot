# trading_bot backups

## Why

As of 2026-08-26 there was **no backup of `trading_bot` of any kind** — no
`archive_mode`, no replication slot, no dump, no backup tooling, no second
disk. ~21 GB of irreplaceable collected series (months of it) existed on one LV
on one NVMe, in a house whose breaker tripped twice in two days.

Corruption is *not* the risk here: `data_checksums=on`, `fsync=on`,
`full_page_writes=on`, `synchronous_commit=on`, and `checksum_failures = 0`
across the cluster's entire life. Both power losses cost only in-flight writes.
**The single copy is the risk.**

## Design

| | what | when | size | retention |
|---|---|---|---|---|
| **incremental** | one closed UTC day of each raw table | nightly 00:20 UTC | ~0.46 GB | remote: all (~180 GB/yr); local: 3 days |
| **full** | entire database, `pg_dump -Fc -Z zstd:3` | Sun 03:30 UTC | ~3.98 GB | remote: 4; local: 1 |

A nightly *full* would not scale — the dump grows ~0.5 GB/day (`carry_book_l2`
alone adds ~17.7 M rows/day), so 7-day retention would be ~40 GB now and ~350 GB
by November. The raw tables are append-only and `ts`-keyed, so a closed-day
window is stable, reproducible, and checksummable.

### Table classification (`lib.sh`)

- **`INCR_TABLES`** — append-only, `ts`-keyed. Windowed `COPY … WHERE ts >= day
  AND ts < day+1`.
- **`SNAP_TABLES`** — dumped whole every night. A table lands here if it has no
  `ts` column (`paper_carry_positions` keys on `opened_ts`/`closed_ts`) **or**
  its rows are UPDATEd after insert (`carry_funding_intervals` 2092 updates,
  `paper_carry_positions` 24, `paper_carry_events` 1). A `ts`-window would
  silently lose those updates. All are small; a whole-table copy is cheaper
  than being subtly wrong.
- **`REBUILDABLE_TABLES`** — derived/telemetry; ride the weekly full only.

`check_unclassified()` fails the run if the database grows a table that is in
none of the three lists. A new collector's data silently never being backed up
is the exact failure this is here to prevent.

## Guarantees

- **Atomic**: every artifact is written to `.tmp`, `sync`ed, and renamed only on
  success. A truncated file is never mistaken for a good one.
- **Idempotent**: re-running a day reuses artifacts whose sha256 still matches
  the manifest and whose row count still agrees with the database.
- **Manifested**: `manifests/incr-YYYY-MM-DD.json` and `manifests/full-*.json`
  record per table — mode, time window, row count, byte size, sha256, column
  order, duration. A restore can be verified without reading the data.
- **Loud**: any failed table is named in the manifest's `failed[]` and the run
  exits non-zero. Nothing is ever silently skipped.
- **Verified on the remote**: `ship.sh` reads the sha256 back off the
  destination. "rsync said OK" is not verification.

## Files

    lib.sh                    config, table lists, shared helpers
    backup-incremental.sh     nightly    [YYYY-MM-DD]
    backup-full.sh            weekly     [--keep-local]
    ship.sh                   tier 1 (Mac over Tailscale) + tier 2 (offsite, stub)
    restore-verify.sh         restore one table into a scratch DB and compare
    systemd/                  units + timers

Config overrides go in `/etc/mexc-backup.conf` (not in the repo).

## Destinations

**Tier 1 — the Mac, `100.73.144.101` over Tailscale.** Protects against this
machine dying. Currently **not enabled**: the Mac's sshd is off and this server
has no private key. To enable, on the Mac:

1. System Settings → General → Sharing → **Remote Login: ON**
2. `mkdir -p ~/mexc-backups`
3. append `/root/.ssh/id_ed25519_mexcbackup.pub` (from trading-server) to
   `~/.ssh/authorized_keys`

then on the server: `SHIP_ENABLED=1` and `SHIP_REQUIRED=1` in
`/etc/mexc-backup.conf`.

**Tier 2 — offsite object store. STUBBED.** Only this tier survives the house.
~180 GB/yr at B2/S3-IA is on the order of $1/month. See the `HOOK` comment in
`ship.sh`.

Until tier 1 is enabled the backups sit on the disk they are protecting, which
is better than nothing and is not the goal.

## Restoring

Whole database from a weekly full:

    createdb trading_bot_restored
    pg_restore -d trading_bot_restored --no-owner --no-privileges \
      /var/backups/mexc/full/trading_bot-<STAMP>.dump

One day of one table from an increment (the schema ships with every night's
set, so a full is not required):

    zstd -dc _schema.sql.zst | psql -d target
    zstd -dc carry_book_l2.incr.zst | psql -d target \
      -c "COPY carry_book_l2 (<columns from the manifest>) FROM STDIN"

To rebuild from scratch: restore the most recent weekly full, then replay every
incremental day after it in date order.

## Verifying

    ops/backup/restore-verify.sh 2026-08-25 venue_funding_snapshots

Restores that table's increment into a throwaway database and compares row
count and a full-relation md5 against the live source over the same window.
A backup that has never been restored is a hypothesis.
