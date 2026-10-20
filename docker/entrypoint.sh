#!/bin/sh
# Waits for PostgreSQL, applies migrations (web only), collects static files, then runs the command.
set -e

python - <<'PY'
import os, sys, time
import dj_database_url
cfg = dj_database_url.parse(os.environ["DATABASE_URL"])
if "postgres" in cfg["ENGINE"]:
    import psycopg
    for attempt in range(40):
        try:
            psycopg.connect(dbname=cfg["NAME"], user=cfg["USER"], password=cfg["PASSWORD"], host=cfg["HOST"], port=cfg["PORT"] or 5432, connect_timeout=3).close()
            break
        except Exception as exc:
            print(f"Waiting for database ({exc.__class__.__name__})...", flush=True)
            time.sleep(2)
    else:
        sys.exit("Database did not become available")
PY

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
