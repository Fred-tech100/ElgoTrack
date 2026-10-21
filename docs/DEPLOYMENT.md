# Deployment (Phase 9)

Stack: PostgreSQL 16, Redis 7, Django under gunicorn, Celery worker + beat, Nginx. Everything is in `docker-compose.yml`.

## First deployment

```bash
cp .env.example .env
# edit: SECRET_KEY (python -c "import secrets;print(secrets.token_urlsafe(64))"), POSTGRES_PASSWORD,
#       ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS (https://your.domain), sender and support email addresses
#       Set DEBUG=false, SECURE_SSL_REDIRECT=true, SECURE_COOKIES=true before serving real traffic.
#       Set EMAIL_PROVIDER=brevo and add BREVO_API_KEY from your deployment secret store.
docker compose up -d --build
docker compose exec web python manage.py createsuperuser     # platform admin
```

The example `.env` is configured for local development. For production, verify `DEBUG=false`,
`SECURE_SSL_REDIRECT=true`, and `SECURE_COOKIES=true` before starting the stack; do not use the temporary
plain-HTTP settings described below on a public deployment.

Keep `.env`, production data, backups, and private TLS keys out of GitHub. Store production values in the deployment
platform's secret manager or protected environment variables, not in repository files. Before the first push, inspect
the staged file list and enable GitHub secret scanning and push protection where available. If a credential was ever
committed, rotate it immediately; deleting the file or rewriting history does not make that credential safe again.

## Transactional email (Brevo)

The app sends through Brevo's transactional email API. In Brevo, create an API key and verify the sender address/domain;
publish Brevo's SPF/DKIM DNS records before production. Configure these environment variables through the deployment
platform's secret manager (never commit the API key):

```env
EMAIL_PROVIDER=brevo
EMAIL_DELIVERY_MODE=celery
BREVO_API_KEY=<Brevo transactional API key>
DEFAULT_FROM_EMAIL=no-reply@elgotrack.site
DEFAULT_FROM_NAME=ElgoTrack
BREVO_SENDER_EMAIL=no-reply@elgotrack.site
BREVO_SENDER_NAME=ElgoTrack
SUPPORT_EMAIL=support@elgotrack.site
ELGOTRACK_PUBLIC_URL=https://your.domain
NOTIFICATION_CHANNELS=in_app,email
```

The sender must be verified in Brevo. `BREVO_SENDER_EMAIL` and `BREVO_SENDER_NAME` select the authenticated sender;
they default to `DEFAULT_FROM_EMAIL` and `DEFAULT_FROM_NAME`. The provider calls Brevo's transactional API over HTTPS
with a 15-second timeout. API keys stay in server-side environment variables and are sent only in the request header.

For deployments with a Celery worker, keep `EMAIL_DELIVERY_MODE=celery` and the Redis broker/worker running;
transactional email is queued after the enclosing database transaction commits, and transient provider failures are
retried. For a single Render free web service without a worker, set `EMAIL_DELIVERY_MODE=sync`: delivery then runs
from the web process after commit and does not require Redis or Render Shell. This can add up to 15 seconds to the
request that triggers an email; failures are recorded on the delivery and safely logged, never marked as sent.
`EMAIL_PROVIDER=django` uses Django's configured backend (console/locmem by default) and does not deliver to customers.
Use `python manage.py test tests.test_emailing` to render and exercise delivery flows locally without a Brevo key.

### Render free web service

In the Render dashboard, open the web service's **Environment** settings and add the following values; use the verified
sender address/domain you configured in Brevo rather than an assumed address:

```text
DEBUG=false
EMAIL_PROVIDER=brevo
EMAIL_DELIVERY_MODE=sync
BREVO_API_KEY=<your Brevo transactional API key>
BREVO_SENDER_EMAIL=<verified sender email>
BREVO_SENDER_NAME=ElgoTrack
DEFAULT_FROM_EMAIL=<same verified sender email>
DEFAULT_FROM_NAME=ElgoTrack
ELGOTRACK_PUBLIC_URL=https://<your Render hostname or custom domain>
```

Save the environment settings and redeploy the web service. Do not set SMTP host, username, password, or port for this
configuration. Confirm that the API key belongs to the Brevo account where the sender is verified, then submit a reset
request for an account you control and check Brevo's transactional logs. Failed attempts are visible in the app's
`EmailDelivery` status/error record and server logs without logging the recipient, message, reset URL, or API key.

Account registration sends a welcome email; new users receive set-password invitations; password reset/change and
role-change security messages use transactional templates. Inventory/overdue-credit alerts are sent only when
`NOTIFICATION_CHANNELS` includes `email`, and are deduplicated using the existing notification records. Invoice,
receipt, and customer-statement emails are explicit user actions and attach the existing tenant-scoped PDF. The
`emailing` app records delivery metadata. Email verification and opt-in digest preferences are not currently part of
the account model and are intentionally not introduced by this rollout.

The `web` container waits for PostgreSQL, runs `migrate` and `collectstatic`, then starts gunicorn. `worker` and `beat` run
Celery (notifications every 30 min, nightly integrity check, session clean-up). Health: `/health/` (liveness) and
`/health/ready/` (database + cache); Compose uses the latter.

## First sign-in of the platform administrator

In production `REQUIRE_2FA_PLATFORM_ADMIN` is on: after `createsuperuser`, the first sign-in is redirected to **Account security** to
scan a QR code with an authenticator app and save the recovery codes. Do this before handing the server over.

## QR verification and the camera

Set `SITE_URL=https://your.domain` in `.env`: it is printed inside every invoice/receipt QR code. The in-app scanner needs HTTPS because
browsers only allow camera access on secure origins (photo upload and typed codes work anywhere). Old documents keep working as long as
that address keeps working, so choose it carefully.

## TLS

* Behind a load balancer that terminates TLS: keep the default `deploy/nginx/nginx.conf`; make sure the balancer sends
  `X-Forwarded-Proto: https`.
* Terminating TLS in Nginx: place `fullchain.pem` and `privkey.pem` in `deploy/certs/` (e.g. from certbot) and set
  `NGINX_CONF=./deploy/nginx/nginx-tls.conf.example` in `.env`.
* To smoke-test over plain HTTP before TLS exists, temporarily set `SECURE_SSL_REDIRECT=false` and `SECURE_COOKIES=false`.
  Remove both before going live.

## Upgrades

```bash
git pull && docker compose build && docker compose up -d      # web re-runs migrations on start
docker compose exec web python manage.py sync_roles           # only when release notes add permissions
docker compose exec web python manage.py check_integrity
```
Take a backup first (below). Migrations are forward-only.

## Backups and restore

```bash
docker compose --profile backup up -d backup     # daily pg_dump (+ media archive) into ./backups, 14-day retention
./scripts/backup.sh                              # or run once by hand (needs DATABASE_URL, pg_dump)
./scripts/restore.sh backups/db-YYYYMMDD-HHMMSS.dump backups/media-YYYYMMDD-HHMMSS.tgz
```
`backup.sh` verifies each dump with `pg_restore --list` before keeping it. Copy `./backups` off the server (object storage,
another machine). **Rehearse a restore into an empty database before you need it**, then run `manage.py check_integrity`.
Keep `.env` in a separate secure store: sessions and reset links depend on `SECRET_KEY`.

## Performance notes

* On PostgreSQL the `products.0002_trigram_search` migration creates `pg_trgm` indexes for contains-search; if the database user cannot
  create the extension the migration skips it safely. Check with `EXPLAIN` on a staging copy of real data.
* Keep Redis enabled (shared cache for throttling and dashboard charts) and use several gunicorn workers.

## Scaling notes

* `GUNICORN_WORKERS` (default 2×CPU+1). Add more `web` replicas behind Nginx if needed; state lives in PostgreSQL/Redis/media volume.
* Media is a local volume; for several hosts switch Django `STORAGES["default"]` to S3-compatible storage (keep receipts private).
* Without Docker: run gunicorn with `docker/gunicorn.conf.py`, `celery -A config worker`, `celery -A config beat`, or replace
  Celery with cron calling `manage.py generate_notifications` and `manage.py check_integrity`.

## Verification status

The compose file, Nginx configs, shell scripts and CI workflow are syntax-checked by the test-suite (YAML parse, `sh -n`, brace
balance, required directives). **They have not been executed here: no Docker daemon or PostgreSQL was available.** Run
`docker compose up --build` on a staging host and `python manage.py test tests` against PostgreSQL (the CI workflow does both
the tests and the concurrency test) before go-live.
