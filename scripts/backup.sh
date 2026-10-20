#!/bin/sh
# Database + media backup with retention. Usage: DATABASE_URL=... ./scripts/backup.sh
# Env: BACKUP_DIR (default ./backups), MEDIA_DIR (default ./media), BACKUP_KEEP_DAYS (default 14)
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
MEDIA_DIR="${MEDIA_DIR:-./media}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

pg_dump --format=custom --no-owner --file="$BACKUP_DIR/db-$STAMP.dump" "$DATABASE_URL"
# refuse to keep an empty/corrupt dump
pg_restore --list "$BACKUP_DIR/db-$STAMP.dump" > /dev/null
if [ -d "$MEDIA_DIR" ]; then
  tar -czf "$BACKUP_DIR/media-$STAMP.tgz" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
fi
find "$BACKUP_DIR" -name 'db-*.dump' -mtime +"$KEEP" -delete
find "$BACKUP_DIR" -name 'media-*.tgz' -mtime +"$KEEP" -delete
echo "Backup complete: $BACKUP_DIR/db-$STAMP.dump"
