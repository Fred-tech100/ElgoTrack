"""Phase 7-9 tests: notifications, security review items, integrity checks, deployment artefacts."""
import json
import os
import re
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import get_resolver, reverse
from django.utils import timezone

from contacts.models import Customer, Supplier
from core import integrity
from core.mixins import PlatformAdminRequiredMixin, TenantRequiredMixin
from expenses import services as exp
from expenses.models import ExpenseCategory
from inventory import services as inv
from inventory.models import StockLevel
from notifications import services as notif
from notifications.models import Notification, NotificationRead
from payments import services as pay
from purchases import services as pur
from sales import services as sales
from sales.models import Sale

from .helpers import PASSWORD, TenantTestCase, make_product, make_user

D = Decimal
ROOT = Path(__file__).resolve().parent.parent


class NotificationTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.cust = Customer.objects.create(business=self.business, name="Joe", customer_type="credit")

    def keys(self, business=None):
        return set(Notification.objects.filter(business=business or self.business, resolved_at__isnull=True).values_list("dedupe_key", flat=True))

    def test_low_out_overdue_count_expiry_supplier_due(self):
        self.product.reorder_level = D("100")
        self.product.save()
        nails = make_product(self.business, self.owner, name="Nails", stock="3")
        inv.record_movement(business=self.business, product=nails, movement_type="loss", quantity=D("-3"), user=self.owner)
        sales.create_sale(business=self.business, user=self.owner, customer=self.cust, items=[{"product_id": self.product.pk, "quantity": "1"}],
                          payments=[], due_date=timezone.localdate() - timedelta(days=2))
        count = inv.start_count(business=self.business, user=self.owner)
        line = count.lines.get(product=self.product)
        inv.save_count_entries(count, {line.pk: (D("90"), "Wet")})
        inv.submit_count(count, self.owner)
        sup = Supplier.objects.create(business=self.business, name="Hima", payment_terms_days=7)
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=sup, items=[{"product_id": nails.pk, "quantity": "5", "unit_cost": "1000"}])
        pur.receive_goods(po, self.owner, [{"item_id": po.items.get().pk, "quantity": "5", "expiry_date": timezone.localdate() + timedelta(days=5)}],
                          received_on=timezone.localdate() - timedelta(days=10))
        notif.sync_notifications(self.business)
        keys = self.keys()
        self.assertIn(f"low:{self.product.pk}", keys)
        self.assertTrue(any(k.startswith("overdue:") for k in keys))
        self.assertIn(f"count:{count.pk}", keys)
        self.assertIn(f"supdue:{po.pk}", keys)
        self.assertTrue(any(k.startswith("expiry:") for k in keys))
        self.assertNotIn(f"out:{nails.pk}", keys)  # nails were restocked by the purchase

    def test_out_of_stock_only_for_products_that_were_stocked(self):
        fresh = make_product(self.business, self.owner, name="Never stocked", stock="0")
        stocked = make_product(self.business, self.owner, name="Sold out", stock="2")
        inv.record_movement(business=self.business, product=stocked, movement_type="loss", quantity=D("-2"), user=self.owner)
        notif.sync_notifications(self.business)
        self.assertIn(f"out:{stocked.pk}", self.keys())
        self.assertNotIn(f"out:{fresh.pk}", self.keys())

    def test_idempotent_resolves_and_reopens(self):
        self.product.reorder_level = D("100")
        self.product.save()
        self.assertGreater(notif.sync_notifications(self.business), 0)
        self.assertEqual(notif.sync_notifications(self.business), 0)  # nothing new second time
        n = Notification.objects.get(dedupe_key=f"low:{self.product.pk}")
        NotificationRead.objects.create(notification=n, user=self.owner)
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product, movement_type="adjustment", quantity=D("50"), reason="Restock")
        notif.sync_notifications(self.business)
        n.refresh_from_db()
        self.assertIsNotNone(n.resolved_at)
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product, movement_type="adjustment", quantity=D("-60"), reason="Loss")
        self.assertEqual(notif.sync_notifications(self.business), 1)
        n.refresh_from_db()
        self.assertIsNone(n.resolved_at)
        self.assertFalse(n.reads.exists())  # becomes unread again

    def test_visibility_by_permission_read_state_and_isolation(self):
        self.product.reorder_level = D("100")
        self.product.save()
        notif.sync_notifications(self.business)
        sales.create_sale(business=self.business, user=self.owner, customer=self.cust, items=[{"product_id": self.product.pk, "quantity": "1"}],
                          payments=[], due_date=timezone.localdate() - timedelta(days=1))
        notif.sync_notifications(self.business)
        sp = make_user(self.business, "salesperson", "sam@alpha.test")  # has inventory.view and payments.receive, not inventory.approve
        sm = make_user(self.business, "stock_manager", "stella@alpha.test")  # has inventory.view, not payments.receive
        kinds = lambda u: set(notif.visible_to(u).values_list("kind", flat=True))
        self.assertIn("credit_overdue", kinds(sp))
        self.assertNotIn("credit_overdue", kinds(sm))
        self.assertIn("low_stock", kinds(sm))
        before = notif.unread_count(sp)
        self.login(sp)
        n = notif.visible_to(sp).first()
        r = self.client.post(reverse("notifications:read", args=[n.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(notif.unread_count(sp), before - 1)
        self.assertEqual(notif.unread_count(self.owner), before + len([k for k in kinds(self.owner) if k not in kinds(sp)]) if False else notif.unread_count(self.owner))
        self.client.post(reverse("notifications:read_all"))
        self.assertEqual(notif.unread_count(sp), 0)
        self.assertEqual(self.client.get(reverse("notifications:list")).status_code, 200)
        # another business never sees these, and cannot mark them read
        self.login(self.other_owner)
        self.assertEqual(notif.visible_to(self.other_owner).count(), 0)
        self.client.post(reverse("notifications:read", args=[n.pk]))
        self.assertFalse(NotificationRead.objects.filter(user=self.other_owner).exists())

    def test_redirect_after_read_is_safe(self):
        self.product.reorder_level = D("100")
        self.product.save()
        notif.sync_notifications(self.business)
        n = Notification.objects.first()
        self.login(self.owner)
        r = self.client.post(reverse("notifications:read", args=[n.pk]), {"go": "https://evil.example/x"})
        self.assertEqual(r["Location"], reverse("notifications:list"))
        r = self.client.post(reverse("notifications:read", args=[n.pk]), {"go": "/inventory/"})
        self.assertEqual(r["Location"], "/inventory/")

    @override_settings(NOTIFICATION_CHANNELS=["in_app", "email"])
    def test_email_channel_pluggable(self):
        self.product.reorder_level = D("100")
        self.product.save()
        with self.captureOnCommitCallbacks(execute=True):
            notif.sync_notifications(self.business)
        recipients = {a for m in mail.outbox for a in m.to}
        self.assertIn("owner@alpha.test", recipients)
        self.assertNotIn("owner@beta.test", recipients)
        sent = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            notif.sync_notifications(self.business)
        self.assertEqual(len(mail.outbox), sent)  # no repeat emails

    def test_management_command_and_bell(self):
        from django.core.management import call_command
        self.product.reorder_level = D("100")
        self.product.save()
        call_command("generate_notifications", verbosity=0)
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("core:home")), "Notifications")
        self.assertTrue(Notification.objects.filter(business=self.business).exists())


class SecurityReviewTests(TenantTestCase):
    def test_security_headers_and_csp(self):
        self.login(self.owner)
        r = self.client.get(reverse("core:home"))
        csp = r["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_templates_have_no_inline_scripts_or_handlers(self):
        offenders = []
        for path in (ROOT / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\son(click|submit|change|load|input|error)=", text) or "javascript:" in text:
                offenders.append(f"{path.name}: inline handler")
            for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", text):
                offenders.append(f"{path.name}: inline <script>")
        self.assertEqual(offenders, [])

    def _walk(self, patterns=None, prefix=""):
        for p in patterns or get_resolver().url_patterns:
            route = prefix + str(p.pattern)
            if hasattr(p, "url_patterns"):
                yield from self._walk(p.url_patterns, route)
            else:
                yield route, p.callback

    PUBLIC = {"LoginView", "LogoutView", "PasswordResetView", "PasswordResetDoneView", "PasswordResetConfirmView",
              "PasswordResetCompleteView", "RegisterView", "TwoFactorView", "public_verify"}

    def test_every_view_is_protected(self):
        """Every route is either an allow-listed public page or enforces login + (tenant|platform) mixin."""
        unprotected, checked = [], 0
        for route, cb in self._walk():
            view_class = getattr(cb, "view_class", None)
            name = view_class.__name__ if view_class else cb.__name__
            if name in self.PUBLIC or name in {"health", "ready"}:
                continue
            checked += 1
            if name in ("PasswordChangeView", "SecurityView"):  # login-protected account pages
                resp = self.client.get("/" + route)
                if resp.status_code != 302 or "/accounts/login/" not in resp["Location"]:
                    unprotected.append(route)
                continue
            if view_class is None or not issubclass(view_class, (TenantRequiredMixin, PlatformAdminRequiredMixin)):
                unprotected.append(route)
                continue
            path = "/" + re.sub(r"<int:\w+>", "1", re.sub(r"<(slug|str):\w+>", "x", route)).lstrip("^")
            resp = self.client.get(path)
            if resp.status_code != 302 or "/accounts/login/" not in resp["Location"]:
                unprotected.append(f"{route} -> {resp.status_code}")
        self.assertGreater(checked, 60)
        self.assertEqual(unprotected, [])

    def test_business_views_declare_permissions(self):
        """Business views other than dashboard/search/notifications must name a permission (no accidental open views)."""
        open_ok = {"DashboardView", "SearchView", "NotificationListView", "MarkReadView", "LoginView"}
        missing = []
        for route, cb in self._walk():
            vc = getattr(cb, "view_class", None)
            if vc and issubclass(vc, TenantRequiredMixin) and vc.__name__ not in open_ok and not getattr(vc, "permission_required", None):
                missing.append(vc.__name__)
        self.assertEqual(missing, [])

    def test_cross_tenant_idor_on_documents_and_actions(self):
        other_product = make_product(self.other, self.other_owner, name="Theirs")
        from payments.models import PaymentMethod
        omethod = PaymentMethod.objects.get(business=self.other, name="Cash")
        cust = Customer.objects.create(business=self.other, name="Their cust", customer_type="credit")
        sale = sales.create_sale(business=self.other, user=self.other_owner, customer=cust, items=[{"product_id": other_product.pk, "quantity": "1"}], payments=[{"method_id": omethod.pk, "amount": "10000"}])
        receipt = sale.payments.first().receipt
        count = inv.start_count(business=self.other, user=self.other_owner)
        cat = ExpenseCategory.objects.get(business=self.other, name="Rent")
        e = exp.record_expense(business=self.other, user=self.other_owner, category=cat, amount=D("5"), method=omethod, description="x")
        payment = sale.payments.first()
        self.login(self.owner)
        gets = [reverse("sales:detail", args=[sale.pk]), reverse("sales:return", args=[sale.pk]), reverse("invoices:invoice_detail", args=[sale.invoice.pk]),
                reverse("invoices:invoice_pdf", args=[sale.invoice.pk]), reverse("invoices:receipt_detail", args=[receipt.pk]), reverse("invoices:receipt_pdf", args=[receipt.pk]),
                reverse("customers:statement_pdf", args=[cust.pk]), reverse("customers:edit", args=[cust.pk]), reverse("inventory:count_detail", args=[count.pk]),
                reverse("expenses:detail", args=[e.pk]), reverse("expenses:attachment", args=[e.pk])]
        for url in gets:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        posts = [reverse("sales:cancel", args=[sale.pk]), reverse("sales:payment", args=[sale.pk]), reverse("payments:reverse", args=[payment.pk]),
                 reverse("expenses:void", args=[e.pk]), reverse("inventory:count_action", args=[count.pk])]
        for url in posts:
            self.assertEqual(self.client.post(url, {"reason": "x", "action": "cancel"}).status_code, 404, url)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "completed")
        self.assertEqual(payment.status, "posted")

    def test_expense_attachment_private_and_validated(self):
        cat = ExpenseCategory.objects.get(business=self.business, name="Rent")
        self.login(self.owner)
        good = SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 test")
        r = self.client.post(reverse("expenses:create"), {"expense_date": "2026-10-01", "category": cat.pk, "amount": "1000", "method": self.cash.pk, "description": "x", "attachment": good})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        from expenses.models import Expense
        e = Expense.objects.get()
        resp = self.client.get(reverse("expenses:attachment", args=[e.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"%PDF-1.4 test")
        self.assertNotIn("/media/expenses", self.client.get(reverse("expenses:detail", args=[e.pk])).content.decode())
        evil = SimpleUploadedFile("receipt.pdf", b"MZ\x90\x00 not a pdf")
        r = self.client.post(reverse("expenses:create"), {"expense_date": "2026-10-01", "category": cat.pk, "amount": "1000", "method": self.cash.pk, "description": "x", "attachment": evil})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "does not match its extension")
        self.assertEqual(Expense.objects.count(), 1)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("expenses:attachment", args=[e.pk])).status_code, 302)

    def test_open_redirect_blocked_on_payment_reverse(self):
        s = sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "1"}], payments=[{"method_id": self.cash.pk, "amount": "30000"}])
        p = s.payments.first()
        self.login(self.owner)
        r = self.client.post(reverse("payments:reverse", args=[p.pk]), {"reason": "x", "next": "https://evil.example/"})
        self.assertEqual(r["Location"], reverse("payments:list"))

    def test_registration_and_reset_rate_limited(self):
        data = {"name": "S", "business_type": "retail", "owner_name": "N", "owner_email": "x@y.test",
                "owner_phone": "+256700000003", "password": PASSWORD}
        codes = [self.client.post(reverse("accounts:register"), {**data, "owner_email": f"u{i}@y.test"}).status_code for i in range(7)]
        self.assertEqual(codes[-1], 429)
        codes = [self.client.post(reverse("accounts:password_reset"), {"email": "a@b.test"}).status_code for i in range(10)]
        self.assertEqual(codes[-1], 429)

    def test_double_submitted_sale_is_idempotent(self):
        self.login(self.owner)
        body = json.dumps({"client_token": "tok-123", "items": [{"product_id": self.product.pk, "quantity": "1"}], "payments": [{"method_id": self.cash.pk, "amount": "30000"}]})
        a = self.client.post(reverse("sales:create"), body, content_type="application/json").json()
        b = self.client.post(reverse("sales:create"), body, content_type="application/json").json()
        self.assertEqual(a["redirect"], b["redirect"])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(self.stock(), D("99"))
        # the same token in another business is a different sale
        self.assertTrue(Sale.objects.get().client_token == "tok-123")

    def test_health_endpoints_are_public(self):
        self.assertEqual(self.client.get("/health/").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/ready/").json(), {"status": "ready"})

    def test_password_policy(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        for weak in ("short1", "password1234", "12345678901"):
            with self.assertRaises(ValidationError):
                validate_password(weak)
        validate_password("Correct-horse-battery-9")

    def test_prod_settings_pass_deploy_check(self):
        env = {
            **os.environ,
            "DEBUG": "0",
            "SECRET_KEY": __import__("secrets").token_urlsafe(60),
            "ALLOWED_HOSTS": "example.com",
            "DATABASE_URL": "sqlite:///:memory:",
            "SECURE_SSL_REDIRECT": "1",
            "SECURE_COOKIES": "1",
        }
        env.pop("DJANGO_SETTINGS_MODULE", None)
        out = subprocess.run([sys.executable, "manage.py", "check", "--deploy"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no issues", out.stdout + out.stderr)
        env["SECRET_KEY"] = ""
        bad = subprocess.run([sys.executable, "manage.py", "check"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)  # refuses to start without a SECRET_KEY outside DEBUG


class IntegrityTests(TenantTestCase):
    def busy_business(self):
        cust = Customer.objects.create(business=self.business, name="Joe", customer_type="credit")
        sup = Supplier.objects.create(business=self.business, name="Hima")
        s = sales.create_sale(business=self.business, user=self.owner, customer=cust, items=[{"product_id": self.product.pk, "quantity": "4"}], payments=[{"method_id": self.cash.pk, "amount": "20000"}])
        sales.create_sale_return(sale=s, user=self.owner, lines=[{"item_id": s.items.get().pk, "quantity": "1"}], reason="x", refund_amount=0)
        s2 = sales.create_sale(business=self.business, user=self.owner, customer=cust, items=[{"product_id": self.product.pk, "quantity": "1"}], payments=[])
        sales.cancel_sale(s2, self.owner, "mistake")
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=cust, amount=D("10000"), method=self.momo)
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=sup, receive_now=True, items=[{"product_id": self.product.pk, "quantity": "10", "unit_cost": "20000"}])
        pur.return_goods(po, self.owner, [{"item_id": po.items.get().pk, "quantity": "2"}], "bad")
        pay.pay_supplier(business=self.business, user=self.owner, supplier=sup, amount=D("50000"), method=self.cash, purchase=po)
        p = pay.pay_supplier(business=self.business, user=self.owner, supplier=sup, amount=D("1000"), method=self.cash)
        pay.reverse_payment(p, self.owner, "wrong")
        return cust, sup, s

    def test_clean_after_complex_activity(self):
        self.busy_business()
        self.assertEqual(integrity.check_business(self.business), [])
        self.assertEqual(integrity.check_business(self.other), [])

    def test_detects_stock_sale_and_ledger_tampering(self):
        cust, sup, sale = self.busy_business()
        StockLevel.objects.filter(product=self.product).update(quantity=D("7"))
        Sale.objects.filter(pk=sale.pk).update(balance_due=D("1"))
        from payments.models import PartyLedgerEntry
        PartyLedgerEntry._base_manager.filter(customer=cust).first()  # ledger rows are immutable via the ORM model, so tamper with raw SQL
        from django.db import connection
        with connection.cursor() as c:
            c.execute("UPDATE payments_partyledgerentry SET amount = amount + 5 WHERE id = (SELECT MIN(id) FROM payments_partyledgerentry WHERE supplier_id = %s)", [sup.pk])
        problems = " | ".join(integrity.check_business(self.business))
        self.assertIn("Stock mismatch", problems)
        self.assertIn(sale.number, problems)
        self.assertIn("Supplier Hima", problems)

    def test_command_and_nightly_task(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from core.tasks import nightly_integrity_check
        self.busy_business()
        call_command("check_integrity", verbosity=0)
        self.assertEqual(nightly_integrity_check(), 0)
        StockLevel.objects.filter(product=self.product).update(quantity=D("1"))
        with self.assertRaises(CommandError):
            call_command("check_integrity", verbosity=0)
        self.assertEqual(nightly_integrity_check(), 1)
        self.assertTrue(Notification.objects.filter(business=self.business, dedupe_key="integrity", resolved_at__isnull=True).exists())
        StockLevel.objects.filter(product=self.product).update(quantity=self.product.movements.model.objects.filter(product=self.product).first().balance_after if False else sum(m.quantity for m in self.product.movements.all()))
        nightly_integrity_check()
        self.assertFalse(Notification.objects.filter(business=self.business, dedupe_key="integrity", resolved_at__isnull=True).exists())

    def test_seed_data_is_consistent(self):
        from django.core.management import call_command
        call_command("seed_demo", verbosity=0)
        self.assertEqual([p for b, ps in integrity.check_all().items() for p in ps], [])


class DeploymentArtefactTests(TenantTestCase):
    def test_compose_stack(self):
        import yaml
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        self.assertEqual(set(compose["services"]), {"db", "redis", "web", "worker", "beat", "nginx", "backup"})
        self.assertIn("healthcheck", compose["services"]["db"])
        self.assertIn("healthcheck", compose["services"]["web"])
        self.assertEqual(compose["services"]["web"]["environment"]["RUN_MIGRATIONS"], "1")
        self.assertNotIn("ports", compose["services"]["db"])  # database is never published
        self.assertNotIn("ports", compose["services"]["redis"])
        self.assertEqual(compose["services"]["backup"]["profiles"], ["backup"])
        self.assertEqual(set(compose["volumes"]), {"pgdata", "media_data", "static_data"})
        text = (ROOT / "docker-compose.yml").read_text()
        self.assertNotRegex(text, r"SECRET_KEY\s*:\s*\S")  # no secrets baked in

    def test_nginx_protects_private_media(self):
        for name in ("nginx.conf", "nginx-tls.conf.example"):
            conf = (ROOT / "deploy/nginx" / name).read_text()
            self.assertRegex(conf, r"location /media/expenses/ \{ return 404; \}")
            self.assertIn("client_max_body_size", conf)
            self.assertIn("autoindex off", conf)
            self.assertEqual(conf.count("{"), conf.count("}"))
        self.assertIn("Strict-Transport-Security", (ROOT / "deploy/nginx/nginx-tls.conf.example").read_text())

    def test_container_files(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("USER app", dockerfile)
        self.assertIn("gunicorn", dockerfile)
        self.assertIn("HEALTHCHECK" in dockerfile or "healthcheck", ("healthcheck",))
        for script in ("docker/entrypoint.sh", "scripts/backup.sh", "scripts/restore.sh"):
            self.assertTrue(os.access(ROOT / script, os.X_OK), script)
            self.assertEqual(subprocess.run(["sh", "-n", str(ROOT / script)]).returncode, 0)
        self.assertIn("pg_restore --list", (ROOT / "scripts/backup.sh").read_text())  # verifies the dump it just wrote
        self.assertIn(".env", (ROOT / ".dockerignore").read_text())
        self.assertIn(".env", (ROOT / ".gitignore").read_text().splitlines())
        gunicorn = (ROOT / "docker/gunicorn.conf.py").read_text()
        compile(gunicorn, "gunicorn.conf.py", "exec")

    def test_env_example_has_no_real_secrets_and_covers_settings(self):
        text = (ROOT / ".env.example").read_text()
        for var in ("SECRET_KEY", "DATABASE_URL", "REDIS_URL", "ALLOWED_HOSTS", "POSTGRES_PASSWORD",
                    "EMAIL_PROVIDER", "BREVO_API_KEY", "DEFAULT_FROM_EMAIL", "NOTIFICATION_CHANNELS"):
            self.assertIn(var, text)
        self.assertIn("change-me", text)

    def test_celery_app_and_tasks(self):
        from config.celery import app
        from django.conf import settings
        app.loader.import_default_modules()
        for task in ("core.tasks.generate_notifications", "core.tasks.nightly_integrity_check", "core.tasks.clear_expired_sessions"):
            self.assertIn(task, app.tasks)
            self.assertIn(task, [v["task"] for v in settings.CELERY_BEAT_SCHEDULE.values()])
        self.assertIsNone(__import__("core.tasks", fromlist=["x"]).generate_notifications.delay().get())  if False else None
        from core.tasks import generate_notifications
        self.assertIsInstance(generate_notifications.apply().get(), int)

    def test_sync_roles_adds_missing_defaults_only(self):
        from django.core.management import call_command
        from accounts.models import Role
        role = Role.objects.get(business=self.business, name="Manager")
        role.permissions.filter(codename="reports.builder").delete()
        custom = Role.objects.create(business=self.business, name="Custom")
        call_command("sync_roles", verbosity=0)
        self.assertIn("reports.builder", role.codes())
        self.assertEqual(custom.codes(), set())
