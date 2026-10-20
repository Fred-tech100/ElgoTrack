from decimal import Decimal

from django.urls import reverse

from core.exceptions import BusinessRuleError, InsufficientStock
from inventory import services as inv
from inventory.models import StockCount, StockMovement

from .helpers import TenantTestCase, make_product


class StockLedgerTests(TenantTestCase):
    def test_opening_balance_creates_movement(self):
        self.assertEqual(self.stock(), Decimal("100"))
        m = self.product.movements.get()
        self.assertEqual((m.movement_type, m.quantity, m.balance_after), ("opening", Decimal("100"), Decimal("100")))

    def test_stock_in_out_and_ledger_sum_matches(self):
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product,
                              movement_type="damage", quantity=Decimal("4"), reason="Broken bags")
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product,
                              movement_type="adjustment", quantity=Decimal("-1"), reason="Miscount")
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product,
                              movement_type="adjustment", quantity=Decimal("10"), reason="Found stock")
        total = sum(m.quantity for m in self.product.movements.all())
        self.assertEqual(self.stock(), Decimal("105"))
        self.assertEqual(total, self.stock())

    def test_reason_required_for_adjustment(self):
        with self.assertRaises(BusinessRuleError):
            inv.manual_adjustment(business=self.business, user=self.owner, product=self.product,
                                  movement_type="loss", quantity=Decimal("1"), reason="")

    def test_negative_stock_prevented(self):
        with self.assertRaises(InsufficientStock):
            inv.record_movement(business=self.business, product=self.product, movement_type="sale",
                                quantity=Decimal("-101"), user=self.owner)
        self.assertEqual(self.stock(), Decimal("100"))
        self.assertEqual(self.product.movements.count(), 1)

    def test_negative_stock_allowed_when_configured(self):
        self.business.allow_negative_stock = True
        self.business.save()
        inv.record_movement(business=self.business, product=self.product, movement_type="sale",
                            quantity=Decimal("-101"), user=self.owner)
        self.assertEqual(self.stock(), Decimal("-1"))

    def test_movements_immutable(self):
        m = self.product.movements.get()
        with self.assertRaises(PermissionError):
            m.quantity = 5
            m.save()
        with self.assertRaises(PermissionError):
            m.delete()

    def test_fractional_quantities(self):
        p = make_product(self.business, self.owner, name="Nails kg", stock="10.5")
        inv.record_movement(business=self.business, product=p, movement_type="sale", quantity=Decimal("-0.25"), user=self.owner)
        self.assertEqual(self.stock(p), Decimal("10.250"))

    def test_adjust_view(self):
        self.login(self.owner)
        r = self.client.post(reverse("inventory:adjust"), {"product": self.product.pk, "movement_type": "loss",
                                                          "quantity": "3", "reason": "Theft"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.stock(), Decimal("97"))

    def test_product_sku_auto_and_unique_per_business(self):
        self.assertTrue(self.product.sku.startswith("SKU-"))
        self.login(self.owner)
        unit = self.product.unit
        data = {"name": "Dup", "sku": self.product.sku, "unit": unit.pk, "purchase_price": "1", "selling_price": "2",
                "min_stock_level": "0", "reorder_level": "0", "tax_rate": "0"}
        r = self.client.post(reverse("products:create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already exists")


class StockCountTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.nails = make_product(self.business, self.owner, name="Nails", stock="100")
        self.hammer = make_product(self.business, self.owner, name="Hammer", stock="20")

    def _count(self):
        count = inv.start_count(business=self.business, user=self.owner)
        return count, {l.product_id: l for l in count.lines.all()}

    def test_full_reconciliation_workflow(self):
        count, lines = self._count()
        self.assertEqual(lines[self.product.pk].expected_quantity, Decimal("100"))
        inv.save_count_entries(count, {
            lines[self.product.pk].pk: (Decimal("96"), "Wet bags"),
            lines[self.nails.pk].pk: (Decimal("100"), ""),
            lines[self.hammer.pk].pk: (Decimal("22"), "Unrecorded return"),
        })
        inv.submit_count(count, self.owner)
        count.refresh_from_db()
        self.assertEqual(count.status, StockCount.SUBMITTED)
        self.assertEqual(self.stock(), Decimal("100"))  # nothing changes until approved
        inv.approve_count(count, self.owner, "ok")
        self.assertEqual(self.stock(), Decimal("96"))
        self.assertEqual(self.stock(self.nails), Decimal("100"))
        self.assertEqual(self.stock(self.hammer), Decimal("22"))
        rec = StockMovement.objects.filter(movement_type="reconciliation")
        self.assertEqual(rec.count(), 2)  # zero-variance line makes no movement
        self.assertEqual(rec.get(product=self.product).source_ref, count.number)

    def test_reason_required_for_variance(self):
        count, lines = self._count()
        inv.save_count_entries(count, {lines[self.product.pk].pk: (Decimal("96"), "")})
        with self.assertRaises(BusinessRuleError):
            inv.submit_count(count, self.owner)

    def test_reject_changes_nothing(self):
        count, lines = self._count()
        inv.save_count_entries(count, {lines[self.product.pk].pk: (Decimal("90"), "Count error")})
        inv.submit_count(count, self.owner)
        with self.assertRaises(BusinessRuleError):
            inv.reject_count(count, self.owner, "")
        inv.reject_count(count, self.owner, "Recount please")
        self.assertEqual(self.stock(), Decimal("100"))
        with self.assertRaises(BusinessRuleError):
            inv.approve_count(count, self.owner)

    def test_variance_applied_not_overwrite_when_sales_happen_during_count(self):
        count, lines = self._count()
        inv.save_count_entries(count, {lines[self.product.pk].pk: (Decimal("96"), "Loss")})
        inv.record_movement(business=self.business, product=self.product, movement_type="sale", quantity=Decimal("-10"), user=self.owner)
        inv.submit_count(count, self.owner)
        inv.approve_count(count, self.owner)
        self.assertEqual(self.stock(), Decimal("86"))  # 100 - 10 sale - 4 variance

    def test_cannot_approve_twice_or_edit_after_submit(self):
        count, lines = self._count()
        inv.save_count_entries(count, {lines[self.product.pk].pk: (Decimal("95"), "x")})
        inv.submit_count(count, self.owner)
        with self.assertRaises(BusinessRuleError):
            inv.save_count_entries(count, {lines[self.product.pk].pk: (Decimal("1"), "x")})
        inv.approve_count(count, self.owner)
        with self.assertRaises(BusinessRuleError):
            inv.approve_count(count, self.owner)
        self.assertEqual(self.stock(), Decimal("95"))

    def test_count_views_and_permissions(self):
        from .helpers import make_user
        stock_mgr = make_user(self.business, "stock_manager", "stella@alpha.test")
        self.login(stock_mgr)
        r = self.client.post(reverse("inventory:count_start"), {"category": "", "notes": "Monthly"})
        count = StockCount.objects.get()
        self.assertRedirects(r, reverse("inventory:count_detail", args=[count.pk]))
        line = count.lines.get(product=self.product)
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {
            "action": "submit", f"counted_{line.pk}": "98", f"reason_{line.pk}": "Damaged"})
        count.refresh_from_db()
        self.assertEqual(count.status, "submitted")
        # stock manager cannot approve
        self.assertEqual(self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "approve"}).status_code, 403)
        mgr = make_user(self.business, "manager", "mia@alpha.test")
        self.login(mgr)
        self.client.post(reverse("inventory:count_action", args=[count.pk]), {"action": "approve", "review_note": "ok"})
        self.assertEqual(self.stock(), Decimal("98"))
        self.assertContains(self.client.get(reverse("inventory:count_detail", args=[count.pk])), "-2")


import threading
import unittest

from django.db import connection
from django.test import TransactionTestCase


@unittest.skipUnless(connection.vendor == "postgresql", "Row-lock concurrency needs PostgreSQL")
class ConcurrencyTests(TransactionTestCase):
    """Two simultaneous sales of the last units must never oversell (run against PostgreSQL)."""

    def test_no_overselling(self):
        from decimal import Decimal
        from django.db import connections
        from .helpers import make_business, make_product
        from inventory.services import record_movement
        business, owner = make_business()
        product = make_product(business, owner, stock="5")
        results = []

        def sell():
            try:
                record_movement(business=business, product=product, movement_type="sale", quantity=Decimal("-3"), user=owner)
                results.append("ok")
            except Exception:
                results.append("fail")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=sell) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        product.stock.refresh_from_db()
        self.assertEqual(sorted(results), ["fail", "ok"])
        self.assertEqual(product.stock.quantity, Decimal("2"))
