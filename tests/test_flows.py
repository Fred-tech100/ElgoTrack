"""End-to-end flows through the real views and forms (not just the services)."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from contacts.models import Customer, Supplier
from expenses.models import ExpenseCategory
from inventory.models import StockMovement
from payments.models import Payment, PaymentMethod
from products.models import Category, Product, Unit
from purchases import services as pur
from purchases.models import PurchaseOrder
from sales import services as sales
from sales.models import Sale

from .helpers import TenantTestCase, make_product, make_user

D = Decimal


class CatalogFlowTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_category_and_unit_crud(self):
        r = self.client.post(reverse("products:category_create"), {"name": "Roofing", "description": "Sheets"})
        self.assertRedirects(r, reverse("products:categories"))
        cat = Category.objects.get(name="Roofing")
        self.assertEqual(self.client.post(reverse("products:category_edit", args=[cat.pk]), {"name": "Roofing & Sheets", "description": ""}).status_code, 302)
        self.assertEqual(self.client.post(reverse("products:category_create"), {"name": "Roofing & Sheets"}).status_code, 200)  # duplicate
        self.assertEqual(self.client.post(reverse("products:unit_create"), {"name": "Roll", "abbreviation": "roll"}).status_code, 302)
        unit = Unit.objects.get(name="Roll")
        self.assertEqual(self.client.get(reverse("products:unit_edit", args=[unit.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("products:categories")), "Roofing")

    def test_product_create_edit_variant_and_opening_stock(self):
        unit = Unit.objects.get(business=self.business, name="Piece")
        base = {"unit": unit.pk, "purchase_price": "100", "selling_price": "150", "min_stock_level": "0", "reorder_level": "5", "tax_rate": "0"}
        r = self.client.post(reverse("products:create"), {**base, "name": "Iron sheet", "variant_label": "28G", "opening_quantity": "40", "is_active": "on"})
        parent = Product.objects.get(name="Iron sheet")
        self.assertRedirects(r, reverse("products:detail", args=[parent.pk]))
        self.assertEqual(self.stock(parent), D("40"))
        child_r = self.client.post(reverse("products:create"), {**base, "name": "Iron sheet", "variant_of": parent.pk, "variant_label": "30G", "is_active": "on"})
        child = Product.objects.get(variant_label="30G")
        self.assertEqual(child.variant_of, parent)
        self.assertEqual(child.display_name, "Iron sheet 30G")
        # a variant cannot itself be a parent
        bad = self.client.post(reverse("products:create"), {**base, "name": "X", "variant_of": child.pk, "is_active": "on"})
        self.assertContains(bad, "main product")
        # barcode uniqueness per business
        self.client.post(reverse("products:create"), {**base, "name": "A", "barcode": "123", "is_active": "on"})
        dup = self.client.post(reverse("products:create"), {**base, "name": "B", "barcode": "123", "is_active": "on"})
        self.assertEqual(dup.status_code, 200)
        r = self.client.post(reverse("products:edit", args=[parent.pk]), {**base, "name": "Iron sheet", "variant_label": "28G", "selling_price": "175", "is_active": "on"})
        parent.refresh_from_db()
        self.assertEqual(parent.selling_price, D("175.00"))
        self.assertTrue(AuditLog.objects.filter(action="product.update", object_id=str(parent.pk)).exists())
        self.assertContains(self.client.get(reverse("products:detail", args=[parent.pk])), "30G")
        # price constraints
        neg = self.client.post(reverse("products:create"), {**base, "name": "Neg", "selling_price": "-1", "is_active": "on"})
        self.assertEqual(neg.status_code, 200)

    def test_product_list_filters_and_sorting(self):
        low = make_product(self.business, self.owner, name="Low item", stock="2", reorder_level=D("5"))
        make_product(self.business, self.owner, name="Zero item", stock="0")
        for params, expect, absent in [({"status": "low"}, "Low item", "Cement"), ({"status": "out"}, "Zero item", "Low item"),
                                       ({"q": "cement"}, "Cement", "Low item"), ({"sort": "-selling_price"}, "Cement", None),
                                       ({"category": "999999"}, None, "Cement")]:
            r = self.client.get(reverse("products:list"), params)
            self.assertEqual(r.status_code, 200)
            if expect:
                self.assertContains(r, expect)
            if absent:
                self.assertNotContains(r, absent)
        self.product.is_active = False
        self.product.save()
        self.assertNotContains(self.client.get(reverse("products:list")), "Cement 50kg")
        self.assertContains(self.client.get(reverse("products:list"), {"status": "inactive"}), "Cement 50kg")

    def test_search_api(self):
        r = self.client.get(reverse("products:search"), {"q": self.product.sku}).json()
        self.assertEqual(r["results"][0]["id"], self.product.pk)
        self.assertEqual(r["results"][0]["stock"], "100")
        self.assertEqual(self.client.get(reverse("products:search"), {"q": "cem"}).json()["results"][0]["name"], "Cement 50kg")
        self.assertGreaterEqual(len(self.client.get(reverse("products:search")).json()["results"]), 1)


class InventoryFlowTests(TenantTestCase):
    def test_stock_pages_filters_and_ledger(self):
        self.login(self.owner)
        make_product(self.business, self.owner, name="Low", stock="1", reorder_level=D("5"))
        self.assertContains(self.client.get(reverse("inventory:stock"), {"status": "low"}), "Low")
        self.assertNotContains(self.client.get(reverse("inventory:stock"), {"status": "low"}), "Cement")
        self.assertEqual(self.client.get(reverse("inventory:stock"), {"q": "ce", "category": "1"}).status_code, 200)
        sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "2"}], payments=[{"method_id": self.cash.pk, "amount": "60000"}])
        r = self.client.get(reverse("inventory:movements"), {"type": "sale", "q": "Cement", "start": "2000-01-01", "end": "2100-01-01"})
        self.assertContains(r, "Sale")
        self.assertContains(self.client.get(reverse("inventory:ledger", args=[self.product.pk])), "SAL-")
        bad = self.client.post(reverse("inventory:adjust"), {"product": self.product.pk, "movement_type": "damage", "quantity": "500", "reason": "x"})
        self.assertContains(bad, "Not enough stock")

    def test_count_cancel_reject_and_wrong_role(self):
        sm = make_user(self.business, "stock_manager", "stella@alpha.test")
        mgr = make_user(self.business, "manager", "mia@alpha.test")
        self.login(sm)
        self.client.post(reverse("inventory:count_start"), {"category": "", "notes": ""})
        from inventory.models import StockCount
        count = StockCount.objects.get()
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "submit"})  # nothing counted
        count.refresh_from_db()
        self.assertEqual(count.status, "draft")
        line = count.lines.get()
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "save", f"counted_{line.pk}": "abc"})  # invalid number
        self.assertEqual(count.lines.get().counted_quantity, None)
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "submit", f"counted_{line.pk}": "90", f"reason_{line.pk}": "Wet"})
        self.login(mgr)
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "reject", "review_note": ""})
        count.refresh_from_db()
        self.assertEqual(count.status, "submitted")  # reason required
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "reject", "review_note": "Recount"})
        count.refresh_from_db()
        self.assertEqual(count.status, "rejected")
        self.assertEqual(self.stock(), D("100"))
        self.assertContains(self.client.get(reverse("inventory:count_detail", args=[count.pk])), "Recount")
        self.login(sm)
        self.client.post(reverse("inventory:count_start"), {"category": "", "notes": ""})
        second = StockCount.objects.exclude(pk=count.pk).get()
        self.client.post(reverse("inventory:count_action", args=[second.pk]), {"action": "cancel"})
        second.refresh_from_db()
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(self.client.get(reverse("inventory:count_list")).status_code, 200)


class PaymentFlowTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.cust = Customer.objects.create(business=self.business, name="Joe", customer_type="credit")
        self.supplier = Supplier.objects.create(business=self.business, name="Hima")
        self.sale = sales.create_sale(business=self.business, user=self.owner, customer=self.cust, items=[{"product_id": self.product.pk, "quantity": "2"}], payments=[])
        self.login(self.owner)

    def test_customer_payment_form_and_sale_payment(self):
        r = self.client.post(reverse("payments:customer_payment"), {"customer": self.cust.pk, "amount": "20000", "method": self.cash.pk, "paid_on": timezone.localdate().isoformat(), "reference": "R1"})
        self.assertEqual(r.status_code, 302)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.balance_due, D("40000"))
        r = self.client.post(reverse("sales:payment", args=[self.sale.pk]), {"amount": "40000", "method": self.momo.pk, "reference": "MM1"})
        self.assertRedirects(r, reverse("sales:detail", args=[self.sale.pk]))
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.payment_status, "paid")
        over = self.client.post(reverse("sales:payment", args=[self.sale.pk]), {"amount": "1", "method": self.momo.pk}, follow=True)
        self.assertContains(over, "only has")
        self.assertContains(self.client.get(reverse("payments:list"), {"direction": "in", "method": self.cash.pk, "q": "Joe", "start": "2000-01-01", "end": "2100-01-01"}), "Joe")
        self.assertContains(self.client.get(reverse("sales:detail", args=[self.sale.pk])), "RCT-")
        self.assertContains(self.client.get(reverse("customers:detail", args=[self.cust.pk])), "Statement")

    def test_invalid_payment_forms(self):
        r = self.client.post(reverse("payments:customer_payment"), {"customer": self.cust.pk, "amount": "0", "method": self.cash.pk, "paid_on": "2026-01-01"})
        self.assertEqual(r.status_code, 200)
        foreign = PaymentMethod.objects.get(business=self.other, name="Cash")
        r = self.client.post(reverse("payments:customer_payment"), {"customer": self.cust.pk, "amount": "5", "method": foreign.pk, "paid_on": "2026-01-01"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    def test_supplier_payment_form(self):
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=self.supplier, receive_now=True, items=[{"product_id": self.product.pk, "quantity": "2", "unit_cost": "1000"}])
        other_sup = Supplier.objects.create(business=self.business, name="Other")
        mismatch = self.client.post(reverse("payments:supplier_payment"), {"supplier": other_sup.pk, "purchase": po.pk, "amount": "500", "method": self.cash.pk, "paid_on": "2026-01-01"})
        self.assertEqual(mismatch.status_code, 200)
        r = self.client.post(reverse("payments:supplier_payment"), {"supplier": self.supplier.pk, "purchase": po.pk, "amount": "500", "method": self.cash.pk, "paid_on": "2026-01-01"})
        self.assertRedirects(r, reverse("suppliers:detail", args=[self.supplier.pk]))
        self.assertEqual(self.supplier.balance, D("1500"))

    def test_reverse_via_view_and_methods_crud(self):
        _, payments = __import__("payments.services", fromlist=["x"]).receive_customer_payment(business=self.business, user=self.owner, customer=self.cust, amount=D("10000"), method=self.cash)
        r = self.client.post(reverse("payments:reverse", args=[payments[0].pk]), {"reason": "Bounced", "next": reverse("payments:list")})
        self.assertRedirects(r, reverse("payments:list"))
        payments[0].refresh_from_db()
        self.assertEqual(payments[0].status, "reversed")
        again = self.client.post(reverse("payments:reverse", args=[payments[0].pk]), {"reason": "again"}, follow=True)
        self.assertContains(again, "already been reversed")
        self.assertEqual(self.client.post(reverse("payments:method_create"), {"name": "Cheque", "sort_order": 9, "is_active": "on"}).status_code, 302)
        m = PaymentMethod.objects.get(business=self.business, name="Cheque")
        self.client.post(reverse("payments:method_edit", args=[m.pk]), {"name": "Cheque", "sort_order": 9})
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        sale_r = self.client.post(reverse("sales:create"), '{"items":[{"product_id":%d,"quantity":"1"}],"payments":[{"method_id":%d,"amount":"30000"}]}' % (self.product.pk, m.pk), content_type="application/json")
        self.assertEqual(sale_r.status_code, 400)  # inactive method rejected
        self.assertEqual(self.client.get(reverse("payments:methods")).status_code, 200)


class SalesPurchaseViewFlowTests(TenantTestCase):
    def test_sales_list_filters_cancel_view(self):
        sp = make_user(self.business, "manager", "mia@alpha.test")
        cust = Customer.objects.create(business=self.business, name="Joe", customer_type="credit")
        s = sales.create_sale(business=self.business, user=sp, customer=cust, items=[{"product_id": self.product.pk, "quantity": "1"}], payments=[], due_date=timezone.localdate() - timedelta_days(1))
        self.login(sp)
        for params in ({"q": "Joe"}, {"payment": "unpaid"}, {"status": "completed"}, {"overdue": "1"}, {"start": "2000-01-01", "end": "2100-01-01"}):
            self.assertContains(self.client.get(reverse("sales:list"), params), s.number)
        self.assertContains(self.client.get(reverse("sales:detail", args=[s.pk])), "Overdue" if False else s.number)
        self.client.post(reverse("sales:cancel", args=[s.pk]), {"reason": ""})
        s.refresh_from_db()
        self.assertEqual(s.status, "completed")  # reason required
        self.client.post(reverse("sales:cancel", args=[s.pk]), {"reason": "Wrong customer"})
        s.refresh_from_db()
        self.assertEqual(s.status, "cancelled")
        self.assertContains(self.client.get(reverse("sales:list"), {"status": "cancelled"}), s.number)
        self.assertContains(self.client.get(reverse("sales:detail", args=[s.pk])), "Wrong customer")
        bad_return = self.client.post(reverse("sales:return", args=[s.pk]), {"reason": "x"})
        self.assertEqual(bad_return.status_code, 200)

    def test_purchase_cancel_return_and_receive_errors_via_views(self):
        sup = Supplier.objects.create(business=self.business, name="Hima")
        self.login(self.owner)
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=sup, items=[{"product_id": self.product.pk, "quantity": "5", "unit_cost": "100"}])
        r = self.client.post(reverse("purchases:receive", args=[po.pk]), {f"qty_{po.items.get().pk}": "99"})
        self.assertContains(r, "still outstanding")
        self.client.post(reverse("purchases:receive", args=[po.pk]), {f"qty_{po.items.get().pk}": "5"})
        r = self.client.post(reverse("purchases:return", args=[po.pk]), {f"qty_{po.items.get().pk}": "2", "reason": "Faulty"})
        self.assertRedirects(r, reverse("purchases:detail", args=[po.pk]))
        self.assertContains(self.client.post(reverse("purchases:return", args=[po.pk]), {f"qty_{po.items.get().pk}": "9", "reason": "x"}), "Cannot return more")
        self.client.post(reverse("purchases:cancel", args=[po.pk]), {"reason": "x"}, follow=True)
        po.refresh_from_db()
        self.assertEqual(po.status, "received")  # cannot cancel once received
        po2 = pur.create_purchase(business=self.business, user=self.owner, supplier=sup, items=[{"product_id": self.product.pk, "quantity": "1", "unit_cost": "1"}])
        self.client.post(reverse("purchases:cancel", args=[po2.pk]), {"reason": "Changed mind"})
        po2.refresh_from_db()
        self.assertEqual(po2.status, "cancelled")
        for params in ({"q": po.number}, {"status": "received"}, {"supplier": sup.pk}):
            self.assertContains(self.client.get(reverse("purchases:list"), params), po.number)
        bad = self.client.post(reverse("purchases:create"), '{"supplier_id": %d, "items": []}' % sup.pk, content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(reverse("purchases:create"), {"supplier": sup.pk}).status_code, 200)

    def test_contacts_crud_and_lists(self):
        self.login(self.owner)
        r = self.client.post(reverse("customers:create"), {"name": "Okello", "phone": "0772", "customer_type": "credit", "credit_limit": "100000", "is_active": "on"})
        c = Customer.objects.get(name="Okello")
        self.assertRedirects(r, reverse("customers:detail", args=[c.pk]))
        self.assertTrue(c.code.startswith("CUS-"))
        self.client.post(reverse("customers:edit", args=[c.pk]), {"name": "Okello J", "phone": "0772", "customer_type": "cash", "credit_limit": "0", "is_active": "on"})
        c.refresh_from_db()
        self.assertEqual(c.customer_type, "cash")
        neg = self.client.post(reverse("customers:create"), {"name": "N", "customer_type": "credit", "credit_limit": "-5", "is_active": "on"})
        self.assertEqual(neg.status_code, 200)
        r = self.client.post(reverse("suppliers:create"), {"name": "Hima", "payment_terms_days": 14, "is_active": "on"})
        s = Supplier.objects.get(name="Hima")
        self.assertTrue(s.code.startswith("SUP-"))
        for url in (reverse("customers:list"), reverse("customers:list") + "?q=Ok&owing=1", reverse("suppliers:list"), reverse("suppliers:list") + "?owing=1",
                    reverse("suppliers:edit", args=[s.pk]), reverse("suppliers:detail", args=[s.pk]) + "?start=2026-01-01&end=2026-12-31"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_expense_categories_and_filters(self):
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse("expenses:category_create"), {"name": "Insurance", "is_active": "on"}).status_code, 302)
        cat = ExpenseCategory.objects.get(business=self.business, name="Insurance")
        self.assertEqual(self.client.post(reverse("expenses:category_edit", args=[cat.pk]), {"name": "Insurance"}).status_code, 302)
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)
        self.client.post(reverse("expenses:create"), {"expense_date": timezone.localdate().isoformat(), "category": ExpenseCategory.objects.get(business=self.business, name="Rent").pk, "amount": "5000", "method": self.cash.pk, "description": "Shop rent"})
        from expenses.models import Expense
        e = Expense.objects.get()
        self.assertContains(self.client.get(reverse("expenses:list"), {"q": "rent", "category": e.category_id}), "Shop rent")
        self.client.post(reverse("expenses:void", args=[e.pk]), {"reason": ""})
        e.refresh_from_db()
        self.assertEqual(e.status, "posted")
        self.client.post(reverse("expenses:void", args=[e.pk]), {"reason": "Duplicate"})
        e.refresh_from_db()
        self.assertEqual(e.status, "void")
        self.assertContains(self.client.get(reverse("expenses:detail", args=[e.pk])), "Duplicate")


def timedelta_days(n):
    from datetime import timedelta
    return timedelta(days=n)


class PlatformAdminFlowTests(TenantTestCase):
    def test_platform_screens_and_user_controls(self):
        from accounts.models import User
        root = User.objects.create_superuser(email="root@x.test", password="Pass-Word-1234!", full_name="Root")
        self.login(root)
        self.assertEqual(self.client.get(reverse("platform:businesses"), {"q": "Alpha", "status": "active"}).status_code, 200)
        self.assertContains(self.client.get(reverse("platform:business_detail", args=[self.business.pk])), "Alpha Hardware")
        r = self.client.post(reverse("platform:business_create"), {"name": "Gamma Agro", "business_type": "agro", "owner_name": "Gina", "owner_email": "gina@gamma.test", "owner_phone": "+256700000002"})
        self.assertEqual(r.status_code, 302)
        from businesses.models import Business
        gamma = Business.objects.get(name="Gamma Agro")
        self.assertEqual(gamma.status, "active")
        self.assertEqual(gamma.phone, "+256700000002")
        self.assertEqual(gamma.roles.count(), 5)
        self.assertEqual(self.client.post(reverse("platform:business_create"), {"name": "Dup", "business_type": "agro", "owner_name": "G", "owner_email": "gina@gamma.test", "owner_phone": "+256700000002"}).status_code, 200)
        self.assertEqual(self.client.post(reverse("platform:business_action", args=[gamma.pk, "explode"])).status_code, 302)
        gina = User.objects.get(email="gina@gamma.test")
        self.client.post(reverse("platform:user_toggle", args=[gina.pk]))
        gina.refresh_from_db()
        self.assertFalse(gina.is_active)
        self.client.post(reverse("platform:user_toggle", args=[root.pk]))
        root.refresh_from_db()
        self.assertTrue(root.is_active)  # cannot deactivate yourself
        from django.core import mail
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("platform:user_reset", args=[self.owner.pk]))
        self.assertIn("/accounts/reset/", mail.outbox[-1].body)
        self.assertEqual(self.client.get(reverse("platform:users"), {"q": "Alpha"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("platform:audit"), {"action": "business", "q": "x"}).status_code, 200)
        self.assertContains(self.client.get(reverse("platform:dashboard")), "Platform overview")

    def test_user_management_by_owner(self):
        self.login(self.owner)
        mgr = make_user(self.business, "manager", "mia@alpha.test")
        r = self.client.post(reverse("accounts:user_edit", args=[mgr.pk]), {"full_name": "Mia M", "email": "hack@x.test", "phone": "", "role": mgr.role_id})
        mgr.refresh_from_db()
        self.assertEqual((mgr.full_name, mgr.email), ("Mia M", "mia@alpha.test"))  # email is immutable here and the form without is_active deactivates
        self.assertFalse(mgr.is_active)
        r = self.client.post(reverse("accounts:user_edit", args=[self.owner.pk]), {"full_name": "Me", "phone": "", "role": self.owner.role_id})
        self.assertEqual(r.status_code, 200)  # cannot deactivate yourself
        self.client.post(reverse("accounts:user_invite", args=[mgr.pk]))
        self.assertEqual(self.client.post(reverse("accounts:role_create"), {"name": "Cashier", "description": "Till"}).status_code, 302)
        self.assertContains(self.client.get(reverse("accounts:roles")), "Cashier")
        self.assertEqual(self.client.get(reverse("accounts:role_edit", args=[self.owner.role_id])).status_code, 200)
