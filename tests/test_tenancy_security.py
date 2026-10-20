from django.urls import reverse

from accounts.models import User
from contacts.models import Customer
from products.models import Product
from sales.services import create_sale

from .helpers import PASSWORD, TenantTestCase, make_product, make_user


class AuthTests(TenantTestCase):
    def test_login_and_logout(self):
        r = self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertRedirects(r, reverse("core:home"))
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 302)

    def test_login_required(self):
        for name in ["products:list", "sales:list", "payments:list", "finance:summary" if False else "reports:summary"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_failed_login_lockout_and_audit(self):
        from audit.models import AuditLog
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": "wrong"})
        r = self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertContains(r, "Too many failed")
        self.assertTrue(AuditLog.objects.filter(action="auth.login_failed").exists())

    def test_password_reset_page(self):
        self.assertEqual(self.client.get(reverse("accounts:password_reset")).status_code, 200)
        r = self.client.post(reverse("accounts:password_reset"), {"email": "owner@alpha.test"})
        self.assertEqual(r.status_code, 302)

    def test_inactive_user_cannot_login(self):
        self.owner.is_active = False
        self.owner.save()
        r = self.client.post(reverse("accounts:login"), {"username": "owner@alpha.test", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class TenantIsolationTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.other_product = make_product(self.other, self.other_owner, name="Paracetamol")
        self.other_customer = Customer.objects.create(business=self.other, name="Beta Customer")

    def test_cannot_open_other_business_objects(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("products:detail", args=[self.other_product.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("products:edit", args=[self.other_product.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("customers:detail", args=[self.other_customer.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("inventory:ledger", args=[self.other_product.pk])).status_code, 404)

    def test_lists_only_show_own_data(self):
        self.login(self.owner)
        r = self.client.get(reverse("products:list"))
        self.assertContains(r, "Cement")
        self.assertNotContains(r, "Paracetamol")
        r = self.client.get(reverse("products:search"), {"q": "Para"})
        self.assertEqual(r.json()["results"], [])

    def test_cannot_sell_other_business_product(self):
        from core.exceptions import BusinessRuleError
        with self.assertRaises(BusinessRuleError):
            create_sale(business=self.business, user=self.owner,
                        items=[{"product_id": self.other_product.pk, "quantity": "1"}],
                        payments=[{"method_id": self.cash.pk, "amount": "1"}])

    def test_sale_json_rejects_other_business_customer(self):
        self.login(self.owner)
        import json
        r = self.client.post(reverse("sales:create"), json.dumps({
            "customer_id": self.other_customer.pk, "items": [{"product_id": self.product.pk, "quantity": "1"}],
            "payments": []}), content_type="application/json")
        self.assertEqual(r.status_code, 404)

    def test_form_choices_are_scoped(self):
        from products.forms import ProductForm
        form = ProductForm(business=self.business)
        self.assertNotIn(self.other_product.category_id, [c.pk for c in form.fields["unit"].queryset if False])
        units = set(form.fields["unit"].queryset.values_list("business_id", flat=True))
        self.assertEqual(units, {self.business.pk})

    def test_other_business_user_cannot_be_edited(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("accounts:user_edit", args=[self.other_owner.pk])).status_code, 404)

    def test_audit_log_scoped(self):
        self.login(self.owner)
        r = self.client.get(reverse("audit:list"))
        self.assertNotContains(r, "owner@beta.test")


class PermissionTests(TenantTestCase):
    def test_salesperson_restrictions(self):
        sp = make_user(self.business, "salesperson", "sam@alpha.test")
        self.login(sp)
        self.assertEqual(self.client.get(reverse("sales:create")).status_code, 200)
        for name in ["reports:summary", "audit:list", "accounts:users", "businesses:settings", "expenses:list",
                     "products:create", "inventory:adjust", "purchases:list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_salesperson_cannot_discount_or_cancel(self):
        from core.exceptions import BusinessRuleError
        sp = make_user(self.business, "salesperson", "sam@alpha.test")
        with self.assertRaises(BusinessRuleError):
            create_sale(business=self.business, user=sp, items=[{"product_id": self.product.pk, "quantity": "1", "discount": "100"}],
                        payments=[{"method_id": self.cash.pk, "amount": "29900"}])
        with self.assertRaises(BusinessRuleError):
            create_sale(business=self.business, user=sp, items=[{"product_id": self.product.pk, "quantity": "1", "unit_price": "100"}],
                        payments=[{"method_id": self.cash.pk, "amount": "100"}])
        sale = create_sale(business=self.business, user=sp, items=[{"product_id": self.product.pk, "quantity": "1"}],
                           payments=[{"method_id": self.cash.pk, "amount": "30000"}])
        self.login(sp)
        self.assertEqual(self.client.post(reverse("sales:cancel", args=[sale.pk]), {"reason": "x"}).status_code, 403)

    def test_privilege_escalation_via_role_edit_blocked(self):
        mgr = make_user(self.business, "manager", "mia@alpha.test")
        self.login(mgr)
        self.assertEqual(self.client.get(reverse("accounts:roles")).status_code, 403)
        self.assertEqual(self.client.post(reverse("accounts:role_edit", args=[mgr.role_id]), {"codes": ["users.manage"]}).status_code, 403)

    def test_owner_role_cannot_be_edited(self):
        self.login(self.owner)
        r = self.client.post(reverse("accounts:role_edit", args=[self.owner.role_id]), {"codes": []})
        self.assertEqual(r.status_code, 403)

    def test_role_permissions_editable(self):
        self.login(self.owner)
        role = make_user(self.business, "salesperson", "sam@alpha.test").role
        self.client.post(reverse("accounts:role_edit", args=[role.pk]), {"codes": ["sales.view", "reports.bogus"]})
        self.assertEqual(role.codes(), {"sales.view"})

    def test_csrf_enforced(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        self.assertEqual(c.post(reverse("sales:cancel", args=[1]), {"reason": "x"}).status_code, 403)

    def test_superuser_goes_to_platform_not_business_pages(self):
        root = User.objects.create_superuser(email="root@x.test", password=PASSWORD, full_name="Root")
        self.login(root)
        r = self.client.get(reverse("products:list"))
        self.assertRedirects(r, reverse("platform:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("platform:dashboard")).status_code, 200)

    def test_business_user_cannot_open_platform(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("platform:dashboard")).status_code, 403)
        self.assertEqual(self.client.post(reverse("platform:business_action", args=[self.other.pk, "suspend"])).status_code, 403)

    def test_suspended_business_blocked_and_reactivated(self):
        root = User.objects.create_superuser(email="root@x.test", password=PASSWORD, full_name="Root")
        self.login(root)
        self.client.post(reverse("platform:business_action", args=[self.business.pk, "suspend"]))
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 403)
        self.login(root)
        self.client.post(reverse("platform:business_action", args=[self.business.pk, "reactivate"]))
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)

    def test_registration_creates_pending_business(self):
        r = self.client.post(reverse("accounts:register"), {"name": "New Shop", "business_type": "retail",
                             "owner_name": "Nina", "owner_email": "nina@new.test", "owner_phone": "+256700000001",
                             "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(r.status_code, 302)
        user = User.objects.get(email="nina@new.test")
        self.assertEqual(user.business.status, "pending")
        self.assertEqual(user.phone, "+256700000001")
        self.assertEqual(user.business.phone, "+256700000001")
        self.client.post(reverse("accounts:login"), {"username": "nina@new.test", "password": PASSWORD})
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 403)

    def test_error_pages(self):
        self.login(self.owner)
        r = self.client.get("/nope/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Page not found", status_code=404)

    def test_audit_is_immutable(self):
        from audit.models import AuditLog
        log = AuditLog.objects.first()
        with self.assertRaises(PermissionError):
            log.save()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_upload_validation(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from core.validators import validate_attachment
        with self.assertRaises(ValidationError):
            validate_attachment(SimpleUploadedFile("evil.exe", b"MZ"))
        with self.assertRaises(ValidationError):
            validate_attachment(SimpleUploadedFile("big.pdf", b"0" * (6 * 1024 * 1024)))
        validate_attachment(SimpleUploadedFile("ok.pdf", b"%PDF"))
