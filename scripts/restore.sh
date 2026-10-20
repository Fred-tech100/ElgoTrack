#!/bin/sh
# Restore a backup into a database. Usage: DATABASE_URL=... ./scripts/restore.sh db-YYYYMMDD-HHMMSS.dump [media-YYYYMMDD-HHMMSS.tgz]
# This REPLACES existing data in the target database. Restore into an empty database or a staging copy first.
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
DUMP="${1:?usage: restore.sh <db dump> [media archive]}"
MEDIA_ARCHIVE="${2:-}"
printf "This will overwrite data in the target database. Type 'restore' to continue: "
read -r answer
[ "$answer" = "restore" ] || { echo "Aborted."; exit 1; }
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$DUMP"
if [ -n "$MEDIA_ARCHIVE" ]; then
  tar -xzf "$MEDIA_ARCHIVE" -C "$(dirname "${MEDIA_DIR:-./media}")"
fi
echo "Restore complete. Run: python manage.py migrate --check && python manage.py check_integrity"
