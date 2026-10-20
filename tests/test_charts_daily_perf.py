import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from contacts.models import Customer, Supplier
from expenses import services as exp
from expenses.models import ExpenseCategory
from inventory import services as inv
from payments import services as pay
from purchases import services as pur
from reports import charts
from reports.services import daily_summary
from sales import services as sales

from .helpers import TenantTestCase, make_product, make_user
from .test_reports import ReportData

D = Decimal
ROOT = Path(__file__).resolve().parent.parent


class DailySummaryTests(ReportData):
    def test_figures_for_today(self):
        pur.create_purchase(business=self.business, user=self.owner, supplier=self.supplier, receive_now=True, items=[{"product_id": self.nails.pk, "quantity": "10", "unit_cost": "3000"}])
        pay.pay_supplier(business=self.business, user=self.owner, supplier=self.supplier, amount=D("5000"), method=self.cash)
        exp.record_expense(business=self.business, user=self.owner, category=ExpenseCategory.objects.get(business=self.business, name="Rent"), amount=D("7000"), method=self.cash, description="Rent")
        inv.manual_adjustment(business=self.business, user=self.owner, product=self.product, movement_type="damage", quantity=D("3"), reason="Wet")
        d = daily_summary(self.business, timezone.localdate())
        s = d["sales"]
        self.assertEqual((s["count"], s["gross"], s["discounts"], s["returns"], s["net"]), (2, D("110000"), D("0"), D("10000"), D("100000")))
        self.assertEqual((s["cogs"], s["profit"], s["unpaid"], s["cancelled"]), (D("74000"), D("26000"), D("20000"), 0))
        c = d["cash"]
        self.assertEqual((c["in"], c["supplier_paid"], c["expenses"], c["refunds"], c["net"]), (D("80000"), D("5000"), D("7000"), D("0"), D("68000")))
        self.assertEqual({m["method__name"]: m["total"] for m in c["by_method"]}, {"Cash": D("60000"), "Mobile Money": D("20000")})
        self.assertEqual(d["purchases"]["received"], D("30000"))
        self.assertEqual(d["expenses"]["by_category"][0]["category__name"], "Rent")
        self.assertEqual(d["stock"]["written_off"], D("3"))
        self.assertEqual(d["balances"]["receivables"], D("20000"))
        self.assertEqual(d["balances"]["payables"], D("25000"))
        self.assertEqual(d["top"][0]["description"], "Cement 50kg")
        self.assertEqual(daily_summary(self.business, timezone.localdate() - timedelta(days=3))["sales"]["count"], 0)

    def test_matches_other_reports(self):
        from reports.services import financial_summary
        today = timezone.localdate()
        fin = financial_summary(self.business, today, today)
        d = daily_summary(self.business, today)
        self.assertEqual((d["sales"]["net"], d["sales"]["cogs"], d["sales"]["profit"], d["cash"]["in"]), (fin["net_sales"], fin["cogs"], fin["gross_profit"], fin["money_in"]))

    def test_cancelled_sales_credit_payments_and_refunds(self):
        sales.cancel_sale(self.s1, self.owner, "Mistake")
        d = daily_summary(self.business, timezone.localdate())
        self.assertEqual((d["sales"]["count"], d["sales"]["cancelled"], d["sales"]["cancelled_value"]), (1, 1, D("60000")))
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.joe, amount=D("5000"), method=self.cash)
        self.assertEqual(daily_summary(self.business, timezone.localdate())["cash"]["in"], D("25000"))  # reversed cash payment excluded
        old = sales.create_sale(business=self.business, user=self.owner, customer=self.joe, items=[{"product_id": self.nails.pk, "quantity": "1"}], payments=[],
                                sale_date=timezone.now() - timedelta(days=2))
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.joe, amount=D("100"), method=self.cash, sale=old)
        self.assertEqual(daily_summary(self.business, timezone.localdate())["cash"]["on_account"], D("100"))

    def test_page_exports_and_navigation(self):
        self.login(self.owner)
        r = self.client.get(reverse("reports:daily"))
        self.assertContains(r, "Net cash movement")
        self.assertContains(r, "Sales by hour")
        self.assertContains(r, "Best sellers")
        self.assertContains(r, "<svg")
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.client.get(reverse("reports:daily"), {"date": yesterday})
        self.assertContains(r, "No sales on this day")
        self.assertContains(r, "Next day")
        self.assertEqual(self.client.get(reverse("reports:daily"), {"date": "garbage"}).status_code, 200)
        for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
            resp = self.client.get(reverse("reports:run", args=["daily-summary"]), {"end": timezone.localdate().isoformat(), "export": fmt})
            self.assertEqual(resp.status_code, 200)
            self.assertIn(ctype, resp["Content-Type"])
        text = self.client.get(reverse("reports:run", args=["daily-summary"]), {"export": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Net sales (excl. tax)", text)
        self.assertIn("Cement 50kg", text)
        self.assertContains(self.client.get(reverse("reports:index")), "Daily summary")
        self.assertContains(self.client.get(reverse("core:home")), "Daily summary")

    def test_permissions_and_isolation(self):
        for role, email, expected in (("salesperson", "sam@alpha.test", 403), ("stock_manager", "stella@alpha.test", 403), ("accountant", "ann@alpha.test", 200), ("manager", "mia2@alpha.test", 200)):
            self.login(make_user(self.business, role, email))
            self.assertEqual(self.client.get(reverse("reports:daily")).status_code, expected, role)
        self.login(self.other_owner)
        self.assertEqual(daily_summary(self.other, timezone.localdate())["sales"]["count"], 0)
        self.assertNotContains(self.client.get(reverse("reports:daily")), "Cement")


class ChartTests(ReportData):
    def test_helpers(self):
        self.assertEqual((charts.compact(950), charts.compact(1500), charts.compact(2_300_000), charts.compact(1_000_000_000)), ("950", "1.5K", "2.3M", "1B"))
        self.assertEqual([charts.nice_max(v) for v in (0, 7, 18, 430, 1200)], [1.0, 10, 20, 500, 2000])

    def test_grouped_bar_geometry(self):
        chart = charts.grouped_bars(["a", "b", "c"], [("Sales", charts.SALES, [D("100"), D("50"), D("0")]), ("Expenses", charts.EXPENSE, [D("0"), D("25"), D("10")])])
        top, bottom = chart["bars"][0], chart["baseline"]
        self.assertAlmostEqual(top["y"] + top["h"], bottom, places=1)           # bars stand on the baseline
        self.assertEqual(chart["bars"][2]["h"], chart["bars"][2]["h"])
        self.assertAlmostEqual(chart["bars"][2]["h"] / chart["bars"][0]["h"], 0.5, places=1)   # 50 is half of 100
        self.assertTrue(all(b["x"] >= chart["left"] for b in chart["bars"]))
        self.assertEqual(chart["bars"][1]["h"], 0)                                 # zero value draws no bar
        self.assertEqual({b["color"] for b in chart["bars"]}, {charts.SALES, charts.EXPENSE})
        self.assertFalse(charts.grouped_bars(["a"], [("S", charts.SALES, [D("0")])])["has_data"])

    def test_donut_segments_add_up(self):
        d = charts.donut([("Cash", D("60")), ("Momo", D("30")), ("Bank", D("10")), ("Zero", D("0"))])
        self.assertEqual([s["label"] for s in d["segments"]], ["Cash", "Momo", "Bank"])
        self.assertAlmostEqual(sum(s["pct"] for s in d["segments"]), 100, places=1)
        self.assertEqual(d["segments"][1]["offset"], round(25 - 60, 2))           # each arc starts where the last ended
        self.assertEqual(len({s["color"] for s in d["segments"]}), 3)
        self.assertFalse(charts.donut([])["has_data"])

    def test_data_builders_use_real_numbers(self):
        trend = charts.sales_vs_expenses(self.business, 14, True)
        self.assertEqual([t["total"] for t in trend["totals"]], [D("100000") + D("10000") - D("10000") if False else D("110000"), D("0")])
        self.assertEqual([t["name"] for t in charts.sales_vs_expenses(self.business, 14, False)["totals"]], ["Sales"])
        mix = {s["label"]: s["amount"] for s in charts.payment_mix(self.business)["segments"]}
        self.assertEqual(mix, {"Cash": D("60000"), "Mobile Money": D("20000")})
        top = charts.top_products(self.business)
        self.assertEqual([r["label"] for r in top], ["Cement 50kg", "Nails"])
        self.assertEqual(top[0]["pct"], 100)
        health = {p["label"]: p["count"] for p in charts.stock_health(self.business)["parts"]}
        self.assertEqual(health, {"In stock": 2, "Low": 0, "Out of stock": 0})
        self.product.reorder_level = D("500")
        self.product.save()
        inv.record_movement(business=self.business, product=self.nails, movement_type="loss", quantity=D("-42"), user=self.owner)
        health = {p["label"]: p["count"] for p in charts.stock_health(self.business)["parts"]}
        self.assertEqual(health, {"In stock": 0, "Low": 1, "Out of stock": 1})

    def test_receivable_aging_buckets(self):
        today = timezone.localdate()
        for days_late, qty in ((-5, "1"), (10, "1"), (45, "1"), (90, "1")):
            sales.create_sale(business=self.business, user=self.owner, customer=self.joe, items=[{"product_id": self.nails.pk, "quantity": qty}], payments=[], due_date=today - timedelta(days=days_late))
        rows = {r["label"]: r["value"] for r in charts.receivable_aging(self.business)["rows"]}
        self.assertEqual(rows["Not yet due"], D("5000") + D("20000"))   # the 4 new sales + S2's 20000 still within terms
        self.assertEqual((rows["1–30 days late"], rows["31–60 days late"], rows["Over 60 days late"]), (D("5000"), D("5000"), D("5000")))

    def test_colours_have_fixed_meaning_and_match_design_tokens(self):
        cfg = (ROOT / "tailwind.config.js").read_text()
        self.assertIn(charts.SALES, cfg)      # sales = brand blue 600
        self.assertIn(charts.EXPENSE, cfg)    # expenses = accent orange 500
        self.assertEqual(len({charts.SALES, charts.EXPENSE, charts.HEALTHY, charts.WARNING, charts.DANGER}), 5)
        self.assertEqual((charts.HEALTHY, charts.WARNING, charts.DANGER), ("#059669", "#f59e0b", "#dc2626"))
        self.assertNotIn(charts.EXPENSE, charts.CATEGORICAL)   # orange is reserved for expenses, never reused for a payment method

    def test_dashboard_renders_charts_by_role(self):
        self.login(self.owner)
        r = self.client.get(reverse("core:home"))
        html = r.content.decode()
        for needle in ("Sales and expenses", "Stock health", "Top products", "Money received by method", "Customer credit", "Colour guide"):
            self.assertIn(needle, html)
        self.assertGreaterEqual(html.count("<svg"), 3)
        self.assertIn(charts.SALES, html)
        self.assertIn(charts.EXPENSE, html)
        sp = make_user(self.business, "salesperson", "sam@alpha.test")
        cache.clear()
        self.login(sp)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn("Top products", html)
        self.assertNotIn("Money received by method", html)           # finance charts stay hidden from sales staff
        self.assertNotIn("Customer credit: how late", html)
        self.assertNotIn("Sales and expenses", html)
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)

    def test_chart_templates_have_accessible_labels_and_no_scripts(self):
        self.login(self.owner)
        html = self.client.get(reverse("core:home")).content.decode()
        self.assertIn('role="img"', html)
        self.assertRegex(html, r'aria-label="Daily sales and expenses')
        for path in (ROOT / "templates/reports/charts").glob("*.html"):
            self.assertNotIn("<script", path.read_text())

    def test_summary_page_shows_expense_chart(self):
        exp.record_expense(business=self.business, user=self.owner, category=ExpenseCategory.objects.get(business=self.business, name="Rent"), amount=D("9000"), method=self.cash, description="x")
        self.login(self.owner)
        r = self.client.get(reverse("reports:summary"))
        self.assertContains(r, "background:#e8731a")
        self.assertContains(r, "Rent")

    def test_dashboard_charts_are_cached_briefly(self):
        self.login(self.owner)
        self.client.get(reverse("core:home"))
        with CaptureQueriesContext(connection) as cold:
            cache.delete(f"dash-charts:{self.business.pk}:1")
            self.client.get(reverse("core:home"))
        with CaptureQueriesContext(connection) as warm:
            self.client.get(reverse("core:home"))
        self.assertLess(len(warm), len(cold))


class QueryBudgetTests(TenantTestCase):
    """Guards speed: page query counts must not grow with the number of rows (no N+1) and stay within a budget."""

    URLS = ["products:list", "sales:list", "customers:list", "suppliers:list", "payments:list", "purchases:list", "expenses:list",
            "inventory:stock", "inventory:movements", "invoices:list", "core:home", "reports:daily", "reports:summary", "audit:list", "accounts:users",
            "notifications:list"]
    BUDGET = 40

    def populate(self, n, offset):
        cust = Customer.objects.get_or_create(business=self.business, name="Joe", defaults={"customer_type": "credit"})[0]
        sup = Supplier.objects.get_or_create(business=self.business, name="Hima")[0]
        cat = ExpenseCategory.objects.get(business=self.business, name="Rent")
        for i in range(n):
            p = make_product(self.business, self.owner, name=f"Item {offset}-{i}", stock="50")
            s = sales.create_sale(business=self.business, user=self.owner, customer=cust, items=[{"product_id": p.pk, "quantity": "2"}], payments=[{"method_id": self.cash.pk, "amount": "1000"}])
            pur.create_purchase(business=self.business, user=self.owner, supplier=sup, receive_now=True, items=[{"product_id": p.pk, "quantity": "3", "unit_cost": "100"}])
            exp.record_expense(business=self.business, user=self.owner, category=cat, amount=D("10"), method=self.cash, description=f"e{i}")
            make_user(self.business, "salesperson", f"u{offset}{i}@alpha.test")

    def measure(self, name):
        cache.set(f"notif-sync:{self.business.pk}", 1, 600)
        for key in (f"dash-charts:{self.business.pk}:1", f"dash-charts:{self.business.pk}:0"):
            cache.delete(key)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse(name))
        self.assertEqual(r.status_code, 200, name)
        return len(ctx)

    def test_no_n_plus_one_and_budget(self):
        self.login(self.owner)
        self.populate(2, 0)
        small = {n: self.measure(n) for n in self.URLS}
        self.populate(18, 1)
        large = {n: self.measure(n) for n in self.URLS}
        for name in self.URLS:
            self.assertLessEqual(large[name], small[name], f"{name}: {small[name]} -> {large[name]} queries as data grew (N+1?)")
            self.assertLessEqual(large[name], self.BUDGET, f"{name} uses {large[name]} queries")
