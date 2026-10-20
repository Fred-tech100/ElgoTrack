# Operations notes

See `docs/DEPLOYMENT.md` for Docker, TLS, upgrades and backups, and `docs/SECURITY.md` for operational security requirements.


## Backups (PostgreSQL)
```bash
pg_dump --format=custom --file=elgonnova-$(date +%F).dump "$DATABASE_URL"
tar czf media-$(date +%F).tgz media/        # logos, product images, expense receipts
```
Schedule daily with cron/systemd; keep offsite copies; test a restore monthly.

## Restore
```bash
createdb elgonnova_restore
pg_restore --no-owner --dbname=elgonnova_restore elgonnova-YYYY-MM-DD.dump
tar xzf media-YYYY-MM-DD.tgz
```
Point `DATABASE_URL` at the restored database, run `python manage.py migrate --check`, then smoke-test sign-in.
Keep `.env` (SECRET_KEY etc.) in a separate secure store: sessions and password-reset tokens depend on SECRET_KEY.

## Production checklist
* `DEBUG=false`, strong `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, HTTPS in front (proxy sets `X-Forwarded-Proto`).
* `python manage.py migrate && python manage.py collectstatic --noinput` (WhiteNoise serves static files).
* Serve `media/` from the proxy/object storage; never execute uploads.
* Run `python manage.py check --deploy`.
* Integrity check (any time): for every product, `SUM(stock movements) = stock level`; for every customer/supplier,
  balance = `SUM(ledger)`. Both are covered by the test suite.
