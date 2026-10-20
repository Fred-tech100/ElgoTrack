from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from contacts.models import Customer, Supplier
from expenses import services as exp
from expenses.models import Expense, ExpenseCategory
from payments import services as pay
from purchases import services as pur
from reports.services import dashboard_metrics, financial_summary
from sales import services as sales

from .helpers import TenantTestCase, make_product, make_user

D = Decimal


class ExpenseTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.cat = ExpenseCategory.objects.get(business=self.business, name="Rent")

    def test_record_and_void_expense(self):
        e = exp.record_expense(business=self.business, user=self.owner, category=self.cat, amount=D("500000"), method=self.cash, description="October rent")
        self.assertTrue(e.number.startswith("EXP-"))
        exp.void_expense(e, self.owner, "Duplicate")
        e.refresh_from_db()
        self.assertEqual(e.status, "void")
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())  # never deleted
        with self.assertRaises(Exception):
            exp.void_expense(e, self.owner, "again")

    def test_invalid_amount_and_views(self):
        self.login(self.owner)
        data = {"expense_date": "2026-10-01", "category": self.cat.pk, "amount": "-5", "method": self.cash.pk, "description": "x"}
        self.assertEqual(self.client.post(reverse("expenses:create"), data).status_code, 200)
        data["amount"] = "12000"
        r = self.client.post(reverse("expenses:create"), data)
        self.assertEqual(r.status_code, 302)
        e = Expense.objects.get()
        self.assertEqual(self.client.get(reverse("expenses:list"), {"start": "2026-09-01", "end": "2026-10-31"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("expenses:detail", args=[e.pk])).status_code, 200)

    def test_other_business_category_rejected(self):
        foreign = ExpenseCategory.objects.get(business=self.other, name="Rent")
        self.login(self.owner)
        r = self.client.post(reverse("expenses:create"), {"expense_date": "2026-10-01", "category": foreign.pk, "amount": "10", "method": self.cash.pk, "description": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Expense.objects.count(), 0)

    def test_accountant_permissions(self):
        acc = make_user(self.business, "accountant", "ann@alpha.test")
        self.login(acc)
        for name in ["expenses:list", "reports:summary", "payments:list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("sales:create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:adjust")).status_code, 403)


class FinancialSummaryTests(TenantTestCase):
    def test_summary_figures(self):
        cust = Customer.objects.create(business=self.business, name="Joe", customer_type="credit")
        supplier = Supplier.objects.create(business=self.business, name="Hima")
        # 3 sold @30000 (cost 25000): 1 paid cash, 2 on credit; then 1 returned
        s = sales.create_sale(business=self.business, user=self.owner, customer=cust,
                              items=[{"product_id": self.product.pk, "quantity": "3", "discount": "3000"}],
                              payments=[{"method_id": self.cash.pk, "amount": "30000"}])
        sales.create_sale_return(sale=s, user=self.owner, lines=[{"item_id": s.items.get().pk, "quantity": "1"}], reason="Faulty")
        nails = make_product(self.business, self.owner, name="Nails", cost="1000", stock="0")
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=supplier,
                                 items=[{"product_id": nails.pk, "quantity": "10", "unit_cost": "1000"}], receive_now=True)
        pay.pay_supplier(business=self.business, user=self.owner, supplier=supplier, amount=D("4000"), method=self.cash, purchase=po)
        exp.record_expense(business=self.business, user=self.owner, category=ExpenseCategory.objects.get(business=self.business, name="Rent"),
                           amount=D("5000"), method=self.cash, description="Rent")
        voided = exp.record_expense(business=self.business, user=self.owner, category=ExpenseCategory.objects.get(business=self.business, name="Rent"),
                                    amount=D("999"), method=self.cash, description="oops")
        exp.void_expense(voided, self.owner, "mistake")
        today = timezone.localdate()
        r = financial_summary(self.business, today - timedelta(days=1), today)
        self.assertEqual(r["gross_sales"], D("90000"))
        self.assertEqual(r["discounts"], D("3000"))
        # returned 1 unit: net 29000 (30000 - 1000 pro-rata discount)
        self.assertEqual(r["returns"], D("29000"))
        self.assertEqual(r["net_sales"], D("58000"))
        self.assertEqual(r["cogs"], D("50000"))      # 2 units net of return * 25000
        self.assertEqual(r["gross_profit"], D("8000"))
        self.assertEqual(r["expenses"], D("5000"))
        self.assertEqual(r["net_operating_result"], D("3000"))
        self.assertEqual(r["purchases"], D("10000"))
        self.assertEqual(r["money_in"], D("30000"))
        self.assertEqual(r["paid_suppliers"], D("4000"))
        self.assertEqual(r["net_cash_movement"], D("30000") - D("4000") - D("5000"))
        # customer: sale 87000 - paid 30000 - return 29000 = 28000 ; supplier: 10000 - 4000
        self.assertEqual(r["receivables"], D("28000"))
        self.assertEqual(r["payables"], D("6000"))
        self.assertEqual(cust.balance, D("28000"))

    def test_date_filtering_excludes_old_sales(self):
        sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "1"}],
                          payments=[{"method_id": self.cash.pk, "amount": "30000"}], sale_date=timezone.now() - timedelta(days=60))
        today = timezone.localdate()
        self.assertEqual(financial_summary(self.business, today - timedelta(days=30), today)["gross_sales"], D("0"))
        self.assertEqual(financial_summary(self.business, today - timedelta(days=90), today)["gross_sales"], D("30000"))

    def test_summary_isolated_between_businesses(self):
        other_product = make_product(self.other, self.other_owner, name="Pills")
        from payments.models import PaymentMethod
        sales.create_sale(business=self.other, user=self.other_owner, items=[{"product_id": other_product.pk, "quantity": "2"}],
                          payments=[{"method_id": PaymentMethod.objects.get(business=self.other, name="Cash").pk, "amount": "60000"}])
        today = timezone.localdate()
        self.assertEqual(financial_summary(self.business, today, today)["gross_sales"], D("0"))
        self.assertEqual(financial_summary(self.other, today, today)["gross_sales"], D("60000"))

    def test_dashboard_and_pages_render(self):
        sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "100"}],
                          payments=[{"method_id": self.cash.pk, "amount": "3000000"}])
        m = dashboard_metrics(self.business)
        self.assertEqual(m["today_sales"], D("3000000"))
        self.assertEqual(m["out_of_stock"], 1)
        self.login(self.owner)
        for name in ["core:home", "reports:summary", "products:list", "products:create", "products:categories", "inventory:stock",
                     "inventory:movements", "inventory:adjust", "inventory:count_list", "inventory:count_start", "customers:list",
                     "customers:create", "suppliers:list", "suppliers:create", "sales:list", "payments:list", "payments:customer_payment",
                     "payments:supplier_payment", "payments:methods", "expenses:list", "expenses:create", "expenses:categories",
                     "invoices:list", "audit:list", "accounts:users", "accounts:roles", "accounts:user_create", "businesses:settings",
                     "accounts:password_change"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)

    def test_settings_update(self):
        self.login(self.owner)
        data = {"name": "Alpha Hardware Ltd", "business_type": "hardware", "email": "a@a.test", "phone": "0700", "address": "Mbale",
                "currency_code": "UGX", "currency_decimals": 0, "tax_name": "VAT", "default_tax_rate": "18", "invoice_prefix": "ALP",
                "receipt_prefix": "RCT", "default_credit_days": 14, "fiscal_year_start_month": 7, "tax_enabled": "on"}
        r = self.client.post(reverse("businesses:settings"), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.business.refresh_from_db()
        self.assertEqual((self.business.invoice_prefix, self.business.tax_enabled), ("ALP", True))
        sale = sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "1"}], payments=[])  if False else None

    def test_user_invitation(self):
        from django.core import mail
        self.login(self.owner)
        role = self.owner.role.__class__.objects.get(business=self.business, name="Salesperson")
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse("accounts:user_create"), {"full_name": "New Seller", "email": "seller@alpha.test", "phone": "", "role": role.pk, "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)
        from accounts.models import User
        u = User.objects.get(email="seller@alpha.test")
        self.assertFalse(u.has_usable_password())
        self.assertEqual(u.business, self.business)
        # cannot assign a role from another business
        foreign = self.other_owner.role
        r = self.client.post(reverse("accounts:user_create"), {"full_name": "X", "email": "x@alpha.test", "role": foreign.pk, "is_active": "on"})
        self.assertEqual(r.status_code, 200)


class SeedDemoTests(TenantTestCase):
    def test_seed_demo_refuses_when_debug_is_off(self):
        from django.core.management import call_command, CommandError
        from django.test import override_settings
        from businesses.models import Business

        with override_settings(DEBUG=False, TESTING=False):
            with self.assertRaises(CommandError):
                call_command("seed_demo", verbosity=0)
        self.assertFalse(Business.objects.filter(is_demo=True).exists())

    def test_seed_demo_runs_and_balances(self):
        from io import StringIO
        from django.core.management import call_command
        from businesses.models import Business
        from contacts.models import Customer
        from inventory.models import StockLevel, StockMovement
        from django.db.models import Sum
        from accounts.models import User

        output = StringIO()
        call_command("seed_demo", stdout=output, verbosity=0)
        first_password = output.getvalue().split("Demo password (generated for this run): ", 1)[1].strip()
        self.assertGreaterEqual(len(first_password), 32)
        self.assertTrue(User.objects.get(email="owner@demo.elgonnova.test").check_password(first_password))
        self.assertTrue(User.objects.get(email="manager@demo.elgonnova.test").check_password(first_password))
        demo = Business.objects.get(is_demo=True)
        self.assertEqual(demo.name, "ElgonNova Demo Hardware")
        from sales.models import Sale
        self.assertGreater(Sale.objects.filter(business=demo).count(), 10)
        # the ledger always agrees with the cached stock level
        for level in StockLevel.objects.filter(business=demo):
            total = StockMovement.objects.filter(product=level.product).aggregate(t=Sum("quantity"))["t"]
            self.assertEqual(level.quantity, total)
        # demo data never leaks into other tenants
        self.assertEqual(Customer.objects.filter(business=self.business).count(), 0)
        reset_output = StringIO()
        call_command("seed_demo", reset=True, stdout=reset_output, verbosity=0)
        second_password = reset_output.getvalue().split("Demo password (generated for this run): ", 1)[1].strip()
        self.assertNotEqual(first_password, second_password)
        self.assertTrue(User.objects.get(email="owner@demo.elgonnova.test").check_password(second_password))
        self.assertEqual(Business.objects.filter(is_demo=True).count(), 1)
