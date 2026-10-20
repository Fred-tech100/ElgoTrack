# Architecture (Phase 1)

## 1. Requirements summary

Multi-tenant SaaS for SMEs. Priority one is **accurate stock control and reconciliation**: system stock vs physical stock
vs variance, every change traceable. Priority two is trustworthy money: customer/supplier balances derived from
transactions, never overwritten. Priority three is isolation: Business A can never see Business B.

## 2. Modules (Django apps)

| App | Responsibility |
|---|---|
| `core` | Base models, tenant mixins, permission catalogue, document numbering, middleware, template tags, error views, `seed_demo` |
| `accounts` | Custom `User` (email login), `Role`, `RolePermission`, login throttling, invitations, user/role screens |
| `businesses` | `Business` (tenant + settings), provisioning defaults, Super Admin platform dashboard and ElgonNova billing, registration/approval |
| `audit` | Append-only `AuditLog`, login/logout/failed-login signals |
| `products` | `Product` (with variants), `Category`, `Unit`, product search API |
| `inventory` | `StockLevel`, immutable `StockMovement` ledger, `StockCount` reconciliation workflow |
| `contacts` | `Customer`, `Supplier` |
| `payments` | `PaymentMethod`, `Payment`, append-only `PartyLedgerEntry`, allocation, reversal, statements |
| `sales` | `Sale`, `SaleItem`, `SaleReturn`, till (POS) |
| `purchases` | `PurchaseOrder`, goods receipts (partial deliveries), purchase returns |
| `expenses` | `Expense`, `ExpenseCategory` |
| `invoices` | `Invoice`, `Receipt`, ReportLab PDF rendering |
| `reports` | Dashboard metrics, financial summary, onboarding checklist (Phase 6 adds the report module) |

Business logic lives in `services.py` modules; views are thin; templates contain no business logic.

## 3. Tenancy and security model

* Every tenant-owned model extends `BusinessScopedModel` (`business` FK, `PROTECT`).
* Views use `TenantRequiredMixin` (login + permission) and `BusinessQuerysetMixin` (`filter(business=request.business)`),
  so cross-tenant object ids return 404. Forms (`BusinessModelForm`/`BusinessForm`) restrict every `ModelChoiceField`
  to the business, so foreign ids cannot be submitted. Services re-check `business` on every record they touch.
* `request.business` is set by `BusinessMiddleware`; suspended/pending businesses get a 403 page.
* Super Admin = Django superuser with `business = NULL` (DB check constraint: non-superusers must have a business).
  Business views redirect superusers to `/platform/`; platform views reject everyone else.
* Platform billing is separate from tenant sales: Super Admins issue ElgonNova invoices to a business or an external
  client, with custom line items and per-line tax. Manually recorded full or partial payments each create a printable
  receipt. The invoice stores client contact snapshots so historical documents remain stable if business details change.
  New business registrations require the owner's phone and use it as the initial business contact number.
* Permissions: catalogue in `core/permissions.py`, stored per role, enforced via `permission_required` (any-of tuple).
* Sensitive actions (discounts/price override, cancellations, returns, reversals, approvals, voids) have their own permissions.
* CSRF on all forms and on the JSON till endpoint (header token). Login throttling (5 failures / 15 min per email+IP,
  plus per-IP ceiling), failed logins audited. Password validators (min 10 chars), Argon2 if installed.
* Production settings: secure cookies, HSTS, SSL redirect, nosniff, X-Frame-Options DENY, no debug pages.
  `manage.py check --deploy` is clean.
* Uploads validated by extension and size (images via Pillow's ImageField verification).
* Audit log is append-only at the model level (save-after-create and delete raise).

## 4. Data model (core relationships)

```
Business 1─* User *─1 Role 1─* RolePermission
Business 1─* Product *─1 Category / Unit / Supplier;  Product 1─1 StockLevel;  Product 1─* StockMovement
Business 1─* StockCount 1─* StockCountLine ─(applied as)→ StockMovement
Business 1─* Customer / Supplier  1─* PartyLedgerEntry   (balance = SUM(amount))
Sale 1─* SaleItem;  Sale 1─* SaleReturn 1─* SaleReturnItem;  Sale 1─1 Invoice
Payment *─1 Sale / PurchaseOrder / Customer / Supplier / Receipt;  Receipt 1─* Payment
PurchaseOrder 1─* PurchaseItem;  1─* GoodsReceipt 1─* GoodsReceiptItem;  1─* PurchaseReturn
Expense *─1 ExpenseCategory, PaymentMethod
DocumentSequence(business, key, year) → gap-free numbers (SAL, INV, RCT, PO, GRN, RET, PRT, EXP, CNT, SKU, CUS, SUP)
```

Integrity: `DecimalField` for all money/quantities (quantities 3 dp), FKs `PROTECT`, per-business unique constraints
(SKU, barcode, document numbers), check constraints (non-negative prices, positive quantities/amounts, exactly one
ledger party, single payment party), indexes on `(business, date)`, `(business, name)` and status/balance lookups.

## 5. Business rules

**Stock**
* `StockLevel.quantity` changes only through `inventory.services.record_movement`, which locks the row
  (`select_for_update`), enforces the negative-stock rule (business setting) and writes an immutable `StockMovement`
  with `balance_after`. `SUM(movements.quantity) == StockLevel.quantity` always (tested, including seeded data).
* Movement types: opening, purchase, sale, sale_return, purchase_return, sale_cancel, adjustment, damage, loss,
  reconciliation (transfer reserved for multi-location).
* Multi-line operations lock products in ascending id order to avoid deadlocks; the whole operation is one transaction.
* Cost: moving-average cost is maintained on receipt; each `SaleItem` snapshots `unit_cost` for profit reporting.

**Stock count (reconciliation)**: start (snapshot of expected qty) → enter physical qty → reasons required for every
difference → submit → authorised user approves/rejects → approval writes `reconciliation` movements for the *variance*
(counted − snapshot), so sales made during the count are not overwritten. Reject/cancel changes nothing.

**Sales**: one atomic transaction creates sale, items, stock movements, customer ledger entry, payments, invoice and
receipt. Credit only for credit customers, within credit limit; walk-in sales must be paid in full. Prices exclude tax;
tax is added per taxable product. Discounts/price overrides need `sales.discount`. Cancellation reverses stock, payments
and ledger (nothing deleted); sales with returns cannot be cancelled. Returns restock, reduce the receivable and may
refund cash.

**Money**: customer/supplier balance = SUM of an append-only ledger (sale +, payment −, return −, refund +,
purchase +, purchase return −, reversal). Customer payments are allocated oldest-sale-first (or to a chosen sale);
overpayment becomes account credit. `Sale.amount_paid/balance_due/payment_status` are caches recomputed from
`Payment`/`SaleReturn` rows. Payments are reversed, never deleted.

**Purchases**: ordering has no stock or money effect; **receiving** (partial deliveries supported) adds stock and
raises what is owed to the supplier; returns reduce both; cancel only if nothing received.

**Financial summary** (Phase 5): net sales (ex-tax, after discounts and returns), COGS from cost snapshots, gross profit,
operating expenses, net operating result, cash movement, receivables, payables — each labelled separately; tax
collected is excluded from revenue. Voided expenses and cancelled sales are excluded.

## 6. UI structure

Sidebar (collapsible on mobile) grouped: Overview · Sales · Stock · Money · Administration. Design system in
`static/src/input.css` (white / deep blue / orange accent as action colour). Till and purchase screens use one
vanilla-JS line-item editor (`static/js/lineitems.js`) posting JSON; every total is recomputed and validated server-side.
Stock count screen is a simple table with numeric inputs suited to phones.

## 7. Phases 6–9 additions

* `reports.engine`: one engine for 24 standard reports and the custom builder (datasets: sales, purchases, stock, payments,
  expenses; grouping by product/category/customer/supplier/user/method/type/day/week/month). All aggregation is in the database;
  `ReportResult` feeds the HTML view and the CSV/Excel/PDF exporters, so exports always equal what is on screen. Sales reports net
  returns and use cost snapshots, and totals are tested to equal the financial summary.
* `notifications`: `Notification` (business-wide, permission-gated, per-user read state, de-duplicated by key, auto-resolved
  when the condition clears) + `Channel` interface. Refreshed by Celery every 30 min and lazily every 10 min on page load.
* `products.importer`: parse → validate everything → preview → atomic commit.
* `core.integrity`: independent re-derivation of stock levels and balances; nightly task and `check_integrity`.
* Deployment: multi-stage Dockerfile, Compose (db, redis, web, worker, beat, nginx, optional backup), Nginx configs (HTTP and
  TLS), gunicorn config, backup/restore scripts, CI workflow with PostgreSQL.

## 7b. Third iteration (security, verification, charts, speed)

* `accounts.totp`, `core.crypto`: TOTP 2FA with encrypted seeds; `TwoFactorEnforcementMiddleware` enforces business/platform policy.
* `invoices.verification`: tokens, short codes, HMAC signatures, lookup and `describe()`; `invoices.verify` has the public page, the scanner
  page and the authenticated lookup API; `core.qr` renders QR as inline SVG (pages) and as vectors (PDF).
* `reports.charts`: pure functions turning queries into SVG/CSS-ready geometry; `reports.services.daily_summary` feeds the daily page and exports.
* Performance: query-budget tests (no N+1; each page ≤ 40 queries and constant as data grows), short-lived dashboard cache, DB connection
  health checks, PostgreSQL trigram indexes for contains-search (migration guarded to PostgreSQL), aggregate-in-database reports.

## 8. Known gaps / next steps

* Verified on SQLite only in this environment. Run the suite against PostgreSQL (CI does) and rehearse `docker compose up`
  and a backup restore on a staging host before go-live. The row-lock concurrency test is skipped on SQLite.
* Batch/expiry is recorded on goods receipts and movements, and expiring batches are reported and notified, but stock is not
  tracked per batch, so there is no FEFO picking and expiry quantities are "as received".
* Single stock location (the `transfer` movement type is reserved). Supplier payments are not auto-allocated to purchases.
* Product pickers in forms are plain selects (fine for hundreds of products; use a search widget for thousands). The till and
  purchase screens use live search.
* SMS/WhatsApp channels (the interface exists); object storage for media is not wired.
* PDF statements/invoices use the built-in Helvetica font (no Unicode beyond Latin-1 in PDFs).
* The UI follows the design brief but was not reviewed in a real browser by a designer.
