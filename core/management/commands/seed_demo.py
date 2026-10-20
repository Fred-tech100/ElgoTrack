"""Create the 'ElgonNova Demo Hardware' demo business with realistic data.

Usage:  python manage.py seed_demo [--reset]
Demo data lives in its own flagged business (is_demo=True) and never touches other tenants.
"""
import random
import secrets
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from businesses.models import Business
from businesses.services import create_business
from contacts.models import Customer, Supplier
from expenses.models import ExpenseCategory
from expenses.services import record_expense
from inventory import services as inv
from inventory.models import StockMovement
from payments import services as pay
from payments.models import PaymentMethod
from products.models import Category, Product, Unit
from purchases import services as pur
from sales import services as sales

DEMO_NAME = "ElgonNova Demo Hardware"

CATALOG = {
    "Cement & Building": [("Cement 50kg", "bag", 32000, 28500, 120), ("Cement 25kg", "bag", 17000, 14800, 60),
                          ("River sand (tonne)", "pc", 90000, 70000, 15), ("Building blocks 6in", "pc", 1800, 1300, 800)],
    "Roofing": [("Iron sheet 28G 3m", "pc", 48000, 41000, 150), ("Iron sheet 30G 3m", "pc", 39000, 33000, 120),
                ("Roofing nails 3in", "kg", 9000, 6800, 80), ("Ridge cap 2m", "pc", 21000, 17000, 40)],
    "Plumbing": [("PVC pipe 1in 6m", "pc", 28000, 22000, 60), ("PVC pipe 2in 6m", "pc", 52000, 44000, 40),
                 ("Water tap brass", "pc", 15000, 11000, 50), ("Teflon tape", "pc", 1500, 900, 200)],
    "Electrical": [("Twin cable 2.5mm (100m)", "pc", 210000, 175000, 20), ("Socket outlet double", "pc", 12000, 8500, 70),
                   ("LED bulb 12W", "pc", 6000, 4000, 150), ("Circuit breaker 20A", "pc", 14000, 10000, 40)],
    "Tools": [("Hammer 16oz", "pc", 25000, 18000, 30), ("Spirit level 60cm", "pc", 30000, 22000, 18),
              ("Padlock 50mm", "pc", 16000, 11000, 45), ("Wheelbarrow", "pc", 140000, 110000, 8)],
    "Paint": [("Emulsion paint 20L white", "pc", 185000, 150000, 25), ("Paint brush 4in", "pc", 7000, 4500, 60)],
}


class Command(BaseCommand):
    help = "Create the ElgonNova Demo Hardware demo business."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the existing demo business first (demo only).")

    @transaction.atomic
    def handle(self, *args, **opts):
        from django.conf import settings

        if not (settings.DEBUG or settings.TESTING):
            raise CommandError("Refusing to create demo accounts with DEBUG off.")
        random.seed(7)
        password = secrets.token_urlsafe(24)
        existing = Business.objects.filter(name=DEMO_NAME, is_demo=True).first()
        if existing:
            if not opts["reset"]:
                raise CommandError("Demo business already exists. Use --reset to rebuild it.")
            self._wipe(existing)
        business, owner = create_business(name=DEMO_NAME, business_type="hardware", owner_email="owner@demo.elgonnova.test",
                                          owner_name="Demo Owner", owner_password=password, is_demo=True,
                                          phone="+256 700 000 000", email="demo@elgonnova.test", address="Plot 12, Republic Street, Mbale",
                                          tax_enabled=False, invoice_terms="Goods once sold are not returnable after 7 days.")
        roles = {r.name: r for r in Role.objects.filter(business=business)}
        for email, name, role in [("manager@demo.elgonnova.test", "Demo Manager", "Manager"),
                                  ("sales@demo.elgonnova.test", "Demo Salesperson", "Salesperson"),
                                  ("stock@demo.elgonnova.test", "Demo Stock Manager", "Stock Manager"),
                                  ("accounts@demo.elgonnova.test", "Demo Accountant", "Accountant")]:
            User.objects.create_user(email=email, password=password, full_name=name, business=business, role=roles[role])
        manager = User.objects.get(email="manager@demo.elgonnova.test")

        suppliers = [Supplier.objects.create(business=business, name=n, phone=p, contact_person=c, payment_terms_days=14)
                     for n, p, c in [("Hima Cement Distributors", "0772 111 222", "Moses"), ("Roofings Uganda Ltd", "0701 333 444", "Alice"),
                                     ("Kampala Plumbing Supplies", "0752 555 666", "Peter"), ("Bright Electricals", "0782 777 888", "Sarah")]]
        customers = [Customer.objects.create(business=business, name=n, phone=p, customer_type=t, credit_limit=Decimal(l))
                     for n, p, t, l in [("Wanyama Construction", "0772 900 100", "credit", "3000000"), ("Nabirye Contractors", "0701 900 200", "credit", "2000000"),
                                        ("Mbale Primary School", "0752 900 300", "credit", "1500000"), ("Okello Joseph", "0782 900 400", "cash", "0"),
                                        ("Namukose Betty", "0772 900 500", "credit", "500000")]]

        units = {u.abbreviation: u for u in Unit.objects.filter(business=business)}
        products = []
        for cat_name, items in CATALOG.items():
            cat = Category.objects.create(business=business, name=cat_name)
            for name, unit, price, cost, stock in items:
                p = Product.objects.create(business=business, name=name, category=cat, unit=units.get(unit, units["pc"]),
                                           selling_price=Decimal(price), purchase_price=Decimal(cost),
                                           wholesale_price=Decimal(int(price * 0.93)), reorder_level=Decimal(max(5, stock // 5)),
                                           supplier=random.choice(suppliers))
                inv.manual_adjustment(business=business, user=owner, product=p, movement_type=StockMovement.OPENING,
                                      quantity=Decimal(stock), reason="Opening stock")
                products.append(p)

        cash = PaymentMethod.objects.get(business=business, name="Cash")
        momo = PaymentMethod.objects.get(business=business, name="Mobile Money")
        bank = PaymentMethod.objects.get(business=business, name="Bank")
        today = timezone.now()

        # Purchases: some paid, some owed
        for i, supplier in enumerate(suppliers):
            chosen = random.sample([p for p in products if p.supplier_id == supplier.pk] or products[:3], k=2)
            po = pur.create_purchase(business=business, user=owner, supplier=supplier, receive_now=True,
                                     order_date=(today - timedelta(days=20 - i * 3)).date(),
                                     items=[{"product_id": p.pk, "quantity": str(random.randint(10, 40)), "unit_cost": str(p.purchase_price)} for p in chosen])
            if i % 2 == 0:
                pay.pay_supplier(business=business, user=owner, supplier=supplier, amount=po.received_value, method=bank, purchase=po)
            elif i == 1:
                pay.pay_supplier(business=business, user=owner, supplier=supplier, amount=po.received_value / 2, method=momo, purchase=po)

        # Sales over the past two weeks
        for day in range(14, -1, -1):
            for _ in range(random.randint(2, 6)):
                picks = random.sample(products, k=random.randint(1, 3))
                items = [{"product_id": p.pk, "quantity": str(random.randint(1, 6))} for p in picks]
                total = sum(Decimal(i["quantity"]) * next(p.selling_price for p in picks if p.pk == i["product_id"]) for i in items)
                on_credit = random.random() < 0.25
                customer = random.choice(customers[:3] + customers[4:]) if on_credit else random.choice([None, None, customers[3]])
                paid = total if not on_credit else (total * Decimal("0.4")).quantize(Decimal("1"))
                try:
                    sales.create_sale(business=business, user=manager, items=items, customer=customer,
                                      payments=[{"method_id": random.choice([cash, momo]).pk, "amount": str(paid)}],
                                      sale_date=today - timedelta(days=day, hours=random.randint(0, 8)))
                except Exception:
                    continue
        for c in customers[:2]:
            if c.balance > 0:
                pay.receive_customer_payment(business=business, user=owner, customer=c, amount=min(c.balance, Decimal("300000")), method=momo)

        cats = {c.name: c for c in ExpenseCategory.objects.filter(business=business)}
        for name, amount, d in [("Rent", 600000, 14), ("Electricity", 185000, 10), ("Transport", 60000, 6), ("Salaries", 900000, 3), ("Airtime", 20000, 1)]:
            record_expense(business=business, user=owner, category=cats[name], amount=Decimal(amount), method=cash,
                           description=f"{name} - demo", expense_date=(today - timedelta(days=d)).date())

        self.stdout.write(self.style.SUCCESS(f"Created {DEMO_NAME}."))
        self.stdout.write(f"Demo password (generated for this run): {password}")

    def _wipe(self, business):
        """Remove only the flagged demo business and everything it owns."""
        from django.apps import apps
        from core.models import DocumentSequence

        assert business.is_demo
        # Immutable ledger tables forbid instance deletes, so use raw deletes (demo tenant only),
        # children before parents.
        children = [("sales.SaleReturnItem", "sale_return__business"), ("sales.SaleItem", "sale__business"),
                    ("purchases.PurchaseReturnItem", "purchase_return__business"),
                    ("purchases.GoodsReceiptItem", "receipt__business"), ("purchases.PurchaseItem", "purchase__business"),
                    ("inventory.StockCountLine", "count__business")]
        for label, lookup in children:
            model = apps.get_model(label)
            model.objects.filter(**{lookup: business})._raw_delete(model.objects.db)
        parents = ["payments.PartyLedgerEntry", "payments.Payment", "invoices.Receipt", "invoices.Invoice", "sales.SaleReturn",
                   "sales.Sale", "purchases.PurchaseReturn", "purchases.GoodsReceipt", "purchases.PurchaseOrder",
                   "expenses.Expense", "expenses.ExpenseCategory", "inventory.StockCount", "inventory.StockMovement",
                   "inventory.StockLevel", "products.Product", "products.Category", "products.Unit", "contacts.Customer",
                   "contacts.Supplier", "payments.PaymentMethod"]
        for label in parents:
            model = apps.get_model(label)
            model.objects.filter(business=business)._raw_delete(model.objects.db)
        User.objects.filter(business=business).delete()
        Role.objects.filter(business=business).delete()
        DocumentSequence.objects.filter(business=business).delete()
        business.delete()
