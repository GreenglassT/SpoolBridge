#!/bin/bash
# backup-spoolman.sh — Timestamped, verified host-side backup of Spoolman's SQLite DB.
#
# Strategy:
#   1. Ask Spoolman to take a consistent snapshot via its backup API. This reads
#      from the live (possibly unlinked) file descriptor, not the on-disk path —
#      which is what saved us on 2026-04-13 when the on-disk file was empty.
#   2. Copy that snapshot out to a timestamped file under data/spoolman/host-backups/.
#   3. Verify the copy: must be > 10 KB and pass PRAGMA integrity_check.
#   4. Prune snapshots older than RETENTION_DAYS.
#
# Intended for daily cron. Exits non-zero on any verification failure.

set -u

SPOOLMAN_URL="http://localhost:7912"
SPOOLMAN_BACKUP_FILE="/home/trent/spoolbridge/data/spoolman/backups/spoolman.db"
DEST_DIR="/home/trent/spoolbridge/data/spoolman/host-backups"
LOG_FILE="${DEST_DIR}/backup.log"
RETENTION_DAYS=30
MIN_BYTES=10240

mkdir -p "$DEST_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

fail() {
    log "ERROR: $*"
    exit 1
}

STAMP=$(date '+%Y-%m-%d_%H%M%S')
DEST_FILE="${DEST_DIR}/spoolman-${STAMP}.db"

log "Starting backup → ${DEST_FILE}"

# 1. Trigger Spoolman's own backup. This is the only way to get a consistent
#    snapshot of the live DB — especially important if the on-disk main file
#    has been unlinked (see 2026-04-13 incident).
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${SPOOLMAN_URL}/api/v1/backup" || true)
if [ "$HTTP_CODE" != "200" ]; then
    fail "Spoolman backup API returned HTTP ${HTTP_CODE}"
fi

if [ ! -f "$SPOOLMAN_BACKUP_FILE" ]; then
    fail "Spoolman backup file missing: ${SPOOLMAN_BACKUP_FILE}"
fi

# 2. Copy to timestamped host-backup file.
cp "$SPOOLMAN_BACKUP_FILE" "$DEST_FILE" || fail "cp failed"

# 3. Verify size and integrity.
SIZE=$(stat -c %s "$DEST_FILE")
if [ "$SIZE" -lt "$MIN_BYTES" ]; then
    fail "Backup too small: ${SIZE} bytes (< ${MIN_BYTES}). Spoolman may be backing up a stale empty file — investigate before trusting."
fi

if command -v sqlite3 >/dev/null 2>&1; then
    CHECK=$(sqlite3 "$DEST_FILE" 'PRAGMA integrity_check;' 2>&1)
    if [ "$CHECK" != "ok" ]; then
        fail "integrity_check failed: ${CHECK}"
    fi
    COUNTS=$(sqlite3 "$DEST_FILE" "SELECT (SELECT COUNT(*) FROM vendor) || '/' || (SELECT COUNT(*) FROM filament) || '/' || (SELECT COUNT(*) FROM spool);")
    log "OK: ${SIZE} bytes, integrity ok, vendors/filaments/spools = ${COUNTS}"
else
    log "OK: ${SIZE} bytes (sqlite3 not on PATH — skipped integrity_check)"
fi

# 4. Prune old backups. Only prune our timestamped files, not the log.
find "$DEST_DIR" -maxdepth 1 -type f -name 'spoolman-*.db' -mtime +"$RETENTION_DAYS" -print -delete | while read -r pruned; do
    log "Pruned ${pruned}"
done

exit 0
