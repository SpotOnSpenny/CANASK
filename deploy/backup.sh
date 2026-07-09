#!/usr/bin/env bash
# CANASK database backup.
#
# Dumps the prod Postgres database, gzips it to a timestamped file, optionally uploads it off-box to
# S3, and prunes old local copies. Designed to run from cron on the Lightsail host (see
# DEPLOY_LIGHTSAIL.md §9).
# Runs independently of the app containers' health -- it only needs the `db` container up -- which is
# what you want from a disaster-recovery backup.
#
# Config via environment (or a values file you `source` before calling):
#   REPO_DIR         path to the repo (default: the repo this script lives in)
#   BACKUP_DIR       where to write dumps      (default: $REPO_DIR/backups)
#   RETENTION_DAYS   prune local dumps older than this (default: 14)
#   BACKUP_S3_URI    optional, e.g. s3://my-bucket/canask  -> uploads each dump there if `aws` is present
#
# Exit non-zero on any failure so cron/monitoring notices.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_S3_URI="${BACKUP_S3_URI:-}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker not found on PATH"
[ -f "$REPO_DIR/app_config/.env.prod" ] || fail "missing $REPO_DIR/app_config/.env.prod"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
OUT="$BACKUP_DIR/canask-$STAMP.sql.gz"

cd "$REPO_DIR"

# Reuse the Makefile target (pg_dump inside the db container) and gzip the stream. `set -o pipefail`
# above makes a pg_dump failure fail the whole pipeline instead of leaving a truncated .gz. -s
# (silent) belt-and-braces with the recipe's @: any make echo on stdout would corrupt the dump.
log "Dumping database -> $OUT"
if ! make -s prod-backup | gzip > "$OUT"; then
    rm -f "$OUT"
    fail "pg_dump failed"
fi

# Guard against a silently-empty dump (e.g. db was starting): a real dump is well over a few hundred bytes.
SIZE="$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")"
[ "$SIZE" -gt 500 ] || fail "dump looks empty ($SIZE bytes): $OUT"
log "Dump OK ($SIZE bytes)"

# Off-box copy (the important part -- an on-box dump doesn't survive losing the instance). A missing
# `aws` CLI is a hard failure: warning-and-exit-0 would let cron record success forever while no dump
# ever leaves the box.
if [ -n "$BACKUP_S3_URI" ]; then
    command -v aws >/dev/null || fail "BACKUP_S3_URI set but 'aws' CLI not found; off-box copy impossible"
    log "Uploading to $BACKUP_S3_URI/"
    aws s3 cp "$OUT" "${BACKUP_S3_URI%/}/canask-$STAMP.sql.gz" || fail "S3 upload failed"
fi

# Prune old local dumps (S3 lifecycle rules should handle remote retention). Non-fatal -- the backup
# itself succeeded -- but never silent: a quietly failing prune fills the disk while logging success.
log "Pruning local dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name 'canask-*.sql.gz' -type f -mtime "+$RETENTION_DAYS" -print -delete \
    || log "WARNING: pruning old dumps failed; local backups are accumulating in $BACKUP_DIR"

log "Backup complete"
