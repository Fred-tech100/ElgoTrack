# Security review (Phase 8)

Reviewed as engineering, QA, database and security roles against the master prompt. Findings were fixed in code and
each fix has a regression test (`tests/test_ops.py`, `tests/test_reports.py`).

## Issues found and fixed

| # | Finding | Fix | Test |
|---|---|---|---|
| 1 | Expense receipts were reachable at a guessable public `/media/...` URL | Served only by an authenticated, tenant-scoped view; Nginx returns 404 for `/media/expenses/` | `test_expense_attachment_private_and_validated`, `test_nginx_protects_private_media` |
| 2 | Open redirect through the `next` parameter on payment reversal (and notifications) | `url_has_allowed_host_and_scheme` | `test_open_redirect_blocked_on_payment_reverse`, `test_redirect_after_read_is_safe` |
| 3 | Uploads validated by extension and size only | Magic-byte check (PNG/JPEG/WebP/PDF), plus Pillow verification for images | `test_upload_validation`, attachment test |
| 4 | Spreadsheet formula injection in CSV/Excel exports (`=cmd…` in a product name) | Text cells starting with `= + - @` are prefixed with `'` | `test_csv_formula_injection_neutralised` |
| 5 | No Content-Security-Policy; inline handlers/scripts in templates | CSP (`script-src 'self'`), all inline JS removed; a test fails if one is added back | `test_security_headers_and_csp`, `test_templates_have_no_inline_scripts_or_handlers` |
| 6 | Registration and password-reset could be hammered | Per-IP rate limiting (429) | `test_registration_and_reset_rate_limited` |
| 7 | A double-tapped "Complete sale" could record two sales | Client idempotency token with a unique constraint | `test_double_submitted_sale_is_idempotent` |
| 8 | `/health/` would be redirected by the SSL redirect, failing container health checks | Health URLs exempt; readiness endpoint checks DB and cache | `test_health_endpoints_are_public` |
| 9 | Stock quantity serialised as `1E+2` in the till search API | Fixed-point formatting | `test_search_api` |
| 10 | Report labels were mangled by `capitalize()` ("Mobile Money" → "Mobile money") | Only enum-like labels are prettified | `test_payment_method_report` |
| 11 | `seed_demo` used a fixed, publicly known password and could be explicitly enabled in production | Generates a per-run random password and refuses to run with DEBUG off | `SeedDemoTests` |

## Added in the second hardening pass

**Authentication**
* Registration, password reset and change all require the password twice, with a show/hide (eye) toggle; passwords are checked against
  length, common-password lists and the user's own name/email. Password forms were previously unstyled and are now consistent.
* **Two-factor authentication (TOTP, RFC 6238)**: the seed is encrypted at rest (Fernet keyed from `SECRET_KEY`), each code works once
  (atomic replay guard), attempts are throttled per user and IP, recovery codes are stored as keyed hashes and shown once, disabling needs
  password + code, and every event is audited. A business owner can **require** 2FA for everyone (users are redirected to set it up);
  platform admins must use it when `REQUIRE_2FA_PLATFORM_ADMIN` is on (the default in production). Owners and platform admins can reset a
  lost device. Verified against the RFC 6238 test vector.
* The pending second-factor session expires after 5 minutes and cannot be forged from outside the password step.

**Document forgery protection (QR verification)**
* Every invoice and receipt gets a 144-bit random token, a human-typable short code (`ABCD-EFGH-JKLM`) and an HMAC-SHA256 signature over
  its immutable facts (type, business, number, date, amount, customer, token), keyed from `SECRET_KEY`.
* The QR code points to `/verify/<token>/`, a public, rate-limited, non-indexed page that shows only what is printed on the document
  (number, date, total, status, masked customer) and is always read from the database, so a doctored or invented paper copy cannot match.
  It reports **Genuine**, **No longer valid** (cancelled sale / reversed payment), **Record altered** (signature mismatch: someone edited the
  stored figures) or **Not recognised** (HTTP 404).
* The in-app scanner (**Verify a document**, permission `invoices.view`) reads the QR with the phone camera (jsQR, vendored, Apache-2.0), from
  a photo, or from a typed code, and shows full details only for the owner's own business; another business's document is reported as
  "not yours" with no details. Every scan is audited. All server text is rendered with `textContent` (no HTML injection).
* Printed PDFs were rasterised at 150 and 300 dpi and decoded with the same jsQR build to prove the codes scan.
* Honest limits: a QR cannot stop someone who copies an **entire genuine** document, QR included, and presents it twice; the scan page shows
  "times verified" so a document verified suspiciously often stands out. It also cannot prove a *person* is who they claim to be.
  Camera scanning needs HTTPS (browser rule); photo and typed-code fallbacks work without it.

**Other**
* `check_integrity` and the nightly task also run against the new verification data; `/verify/lookup/` and the scanner page require login.
* `Permissions-Policy` allows the camera only for the app's own origin.

## Automated guards that keep it that way

* Every URL route is either an allow-listed public page or enforces login + tenant/platform mixin, and anonymous requests
  are redirected (`test_every_view_is_protected`). Every business view names a permission (`test_business_views_declare_permissions`).
* Cross-tenant tests for documents, PDFs, statements, counts, expenses, payments, saved reports and notifications.
* Integrity checker (`manage.py check_integrity`, nightly Celery task): stock level = ledger, cached sale balances = payments
  and returns, customer/supplier ledgers = independently derived balances. Tampering tests prove it detects drift.
* `manage.py check --deploy` is run in the suite with production settings; the app refuses to boot without `SECRET_KEY`.
* `bandit -ll` reports no medium/high issues (CI runs it; the one `mark_safe` for QR SVG is annotated and tested for escaping). Remaining low findings include the non-cryptographic `random` used only for predictable demo data, `assert` in the demo reset, and the dev-only fallback key.

## Repository and deployment secrets

Keep `.env`, databases, uploaded media, backups, and private TLS keys out of source control. The `.gitignore` rules help
prevent accidental additions but do not remove files already tracked or erase secrets from Git history. If any credential
was committed, rotate it immediately and review the repository history before making it public.

Brevo API credentials belong in the deployment secret store. Delivery records retain recipient, event type, status,
provider message ID, and a sanitized error only; message bodies and API credentials are not persisted by the app.

## Operational requirements (not enforceable in code)

* **Set `REDIS_URL` in production.** Login throttling and rate limits use the cache; with the default per-process memory cache,
  each gunicorn worker keeps its own counters. The Compose stack already wires Redis.
* Terminate TLS (Nginx TLS config or a load balancer) and keep `SECURE_SSL_REDIRECT`/`SECURE_COOKIES` on.
* Keep `TRUST_X_FORWARDED_FOR=false` unless the app is only reachable through your proxy.
* Serve `/media/` only through the provided Nginx config (it blocks `/media/expenses/`).
* Run `python manage.py sync_roles` after upgrading an existing installation so built-in roles gain new permissions.

## Known limitations

* CSP allows inline *styles* (`style-src 'unsafe-inline'`) because a few elements use `style=""` for chart bars.
* No per-user session listing.
* Not penetration-tested by a third party. Not tested on PostgreSQL in this environment (see ARCHITECTURE.md).
