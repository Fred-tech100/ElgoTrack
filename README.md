# ElgonNova Inventory & Business Management Platform

Multi-tenant inventory, stock-control, sales, purchasing, credit and expense management for hardware shops,
pharmacies, retail and wholesale businesses. Django + PostgreSQL + Tailwind CSS.

**Delivered in this package: Phases 1–9** — architecture, foundation, inventory, sales & purchases, financial operations,
reporting and exports, UX refinement, security review and testing, and Docker/Nginx/Celery deployment. See [Status](#status).

## Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then set DEBUG=true for local work
python manage.py migrate        # SQLite is used when DATABASE_URL is empty (dev only)
python manage.py createsuperuser   # platform (Super Admin) account
python manage.py seed_demo      # optional: "ElgonNova Demo Hardware" with realistic data
python manage.py runserver
```

* Business users sign in at `/accounts/login/`; the Super Admin lands on `/platform/`.
* `seed_demo` generates a new random password each time and prints it once. Use demo accounts only for local development, never with production data.
* `python manage.py seed_demo --reset` rebuilds only the flagged demo tenant.

### PostgreSQL

```bash
createdb elgonnova
# .env:  DATABASE_URL=postgres://user:password@localhost:5432/elgonnova
python manage.py migrate
```

### Frontend (Tailwind)

The compiled stylesheet is committed at `static/css/app.css`. To change styles:

```bash
npm install
npm run build:css        # or: npm run watch:css
```

Design tokens and component classes (`.btn-*`, `.card`, `.table`, `.badge-*`, `.input`…) live in
`static/src/input.css`; colours (deep blue `brand`, orange `accent`) in `tailwind.config.js`.

### Tests

```bash
python manage.py test tests          # 215 tests; DEBUG not required
coverage run manage.py test tests && coverage report   # ~96% line coverage
```

One concurrency test (two simultaneous sales of the last units) only runs on PostgreSQL because SQLite has no row locks.

## Environment variables

See `.env.example`: `SECRET_KEY` (required unless `DEBUG=true`), `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`DATABASE_URL`, `REDIS_URL`, `EMAIL_PROVIDER`, `EMAIL_DELIVERY_MODE`, `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`,
`BREVO_SENDER_NAME`, `DEFAULT_FROM_EMAIL`, `DEFAULT_FROM_NAME`,
`SUPPORT_EMAIL`, `ELGOTRACK_PUBLIC_URL`, `NOTIFICATION_CHANNELS`, `TRUST_X_FORWARDED_FOR`,
`SECURE_SSL_REDIRECT`, `MEDIA_ROOT`. Development defaults to Django's console email backend; production delivery uses
Brevo's transactional API and the Celery worker. Never commit `.env`, databases, uploaded media, backups, or private certificates.

Before pushing, check `git status` and confirm only intended source and public assets are staged. The repository ignores local
environment files and data, but ignore rules cannot remove files already tracked or erase secrets from Git history. If a secret
was ever committed, rotate it immediately; removing it from the latest commit is not sufficient.

## Roles

Built-in roles are created per business: **Business Owner** (always everything), **Manager**, **Salesperson**,
**Stock Manager**, **Accountant**. Owners can edit permissions of every role except Owner and create custom roles
(*Users & roles*). The **Super Admin** is a Django superuser with no business and no business permissions; it can only
use `/platform/`. Permissions are checked server-side on every view.

## Documentation

* `docs/ARCHITECTURE.md` — requirements, modules, data model, business rules, security model, UI structure.
* `docs/DEPLOYMENT.md` — Docker Compose, TLS, upgrades, backups, scaling.
* `docs/SECURITY.md` — security review findings, fixes and operational requirements.
* `docs/OPERATIONS.md` — day-to-day operations notes.

## Docker (production-style)

```bash
cp .env.example .env     # set production secrets/hosts and DEBUG=false, SECURE_SSL_REDIRECT=true, SECURE_COOKIES=true
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```
See `docs/DEPLOYMENT.md`. `seed_demo` refuses to run with `DEBUG` off.

## What you can track and manage

| Need | Where | Notes |
|---|---|---|
| **Stock levels** | Stock levels, product ledger, stock counts, adjustments | Every change is a ledger entry; counts need approval; low/out alerts |
| **Sales and returns** | New sale (till), Sales, sale detail | Split payments, credit, discounts (permissioned), returns with optional refund, cancellations (stock + payments reversed) |
| **Customer accounts** | Customers, statements (PDF) | Balance is derived from an append-only ledger; credit limits; overdue tracking |
| **Credit and payments** | Payments, receive payment, reversals | Oldest-first allocation; receipts; every reversal audited |
| **Purchases and suppliers** | Purchases, Suppliers, pay supplier | Partial deliveries, returns, supplier statements, payment-due alerts |
| **Daily summary** | Reports → Daily summary | One page per day: sales, returns, money received by method, expenses, purchases, stock, balances; PDF/Excel/CSV |
| **Charts** | Dashboard, Daily summary, Financial summary | Sales vs expenses, stock health, top products, payment mix, credit ageing, sales by hour |

Chart colours always mean the same thing: **blue** sales, **orange** expenses, **green** healthy/paid, **amber** low/due soon, **red** out of stock/overdue (see `docs/DESIGN.md`).

## Security highlights

* Passwords: 10+ characters, common/personal passwords rejected, Argon2, **show/hide eye icon and confirm password** on registration, reset and change.
* **Two-factor authentication** (authenticator app, RFC 6238) with encrypted seeds, replay protection, throttling and one-time recovery codes. Owners can require it for the whole business; platform admins must use it in production.
* **Forgery protection:** every invoice and receipt carries a QR code and short code. Scanning opens a page showing what the system recorded (number, date, total, status); an altered paper copy cannot match. A signature detects edits to the stored figures. Owners and staff can scan inside the system (**Verify a document**, camera needs HTTPS) or type the code. See `docs/SECURITY.md`.
* Tenant isolation at query level, role-based permissions on every view, CSP without inline scripts, rate limiting, audit log, private expense receipts.

## Reports, import/export, notifications, search

* **Reports** (`/reports/`): 24 standard inventory, sales, purchase and finance reports with date/category/product/supplier/
  customer/user/method/type filters, exported to CSV, Excel and PDF; a **custom report builder** (choose data, filters and
  grouping; save reports for reuse).
* **Products**: CSV/Excel import with all-or-nothing validation and a preview, CSV export.
* **Notifications**: low/out of stock, overdue credit, supplier payment due, counts awaiting approval, expiring batches;
  in-app with a channel interface (email included; SMS/WhatsApp can be added by implementing one class).
* **Global search** (`/` to focus): products, customers, suppliers, sales, invoices, receipts, purchases, payments, expenses.
* Upgrading an existing install: `python manage.py migrate && python manage.py sync_roles` (existing invoices and receipts are given verification codes by the migration).
* Set `SITE_URL=https://your.domain` so the QR codes on invoices and receipts always point at your public address.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Architecture | Done (`docs/ARCHITECTURE.md`) |
| 2 | Foundation: tenancy, auth, roles, audit | Done |
| 3 | Products, stock ledger, stock counts | Done |
| 4 | Sales, purchases, customers, suppliers, payments, credit, returns | Done |
| 5 | Expenses, invoices (PDF), receipts (PDF), financial summary | Done |
| 6 | Reports, custom builder, CSV/Excel/PDF export, product import | Done |
| 7 | UX refinement (search, notifications bell, breadcrumbs, sorting, CSP-safe JS, mobile count screen) | Done; not reviewed by a designer in a browser |
| 8 | Security review, integrity checks, expanded tests | Done (`docs/SECURITY.md`) |
| 9 | Docker, Compose, Nginx, gunicorn, Celery, backups, CI | Written and syntax-tested; **not executed here** (no Docker/PostgreSQL available) |
| 10 | Daily summary, colour-coded charts, 2FA, QR verification, performance guards | Done (this release) |

Known gaps are listed at the end of `docs/ARCHITECTURE.md`.
