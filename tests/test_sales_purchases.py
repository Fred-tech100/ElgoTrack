import json
from datetime import date
from decimal import Decimal

from django.urls import reverse

from contacts.models import Customer, Supplier
from core.exceptions import BusinessRuleError, InsufficientStock
from inventory.models import StockMovement
from invoices.models import Invoice, Receipt
from payments import services as pay
from payments.models import Payment, PartyLedgerEntry
from purchases import services as pur
from purchases.models import PurchaseOrder
from sales import services as sales
from sales.models import Sale

from .helpers import TenantTestCase, make_product, make_user

D = Decimal


class SaleTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.credit_cust = Customer.objects.create(business=self.business, name="Joseph", customer_type="credit", credit_limit=D("100000"))
        self.cash_cust = Customer.objects.create(business=self.business, name="Walk Regular", customer_type="cash")

    def sell(self, qty="2", paid="60000", customer=None, **kw):
        return sales.create_sale(business=self.business, user=self.owner,
                                 items=[{"product_id": self.product.pk, "quantity": qty}],
                                 payments=[{"method_id": self.cash.pk, "amount": paid}] if paid else [],
                                 customer=customer, **kw)

    def test_cash_sale_updates_stock_invoice_receipt(self):
        sale = self.sell()
        self.assertEqual(sale.total, D("60000.00"))
        self.assertEqual((sale.payment_status, sale.balance_due), ("paid", D("0")))
        self.assertEqual(self.stock(), D("98"))
        mv = StockMovement.objects.get(source_type="sale", source_id=sale.pk)
        self.assertEqual((mv.quantity, mv.balance_after, mv.unit_cost), (D("-2"), D("98"), D("25000")))
        self.assertEqual(sale.invoice.number.split("-")[0], "INV")
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(sale.items.get().unit_cost, D("25000"))

    def test_multiple_items_discount_tax_and_split_payment(self):
        self.business.tax_enabled = True
        self.business.default_tax_rate = D("18")
        self.business.save()
        self.product.is_taxable = True
        self.product.save()
        nails = make_product(self.business, self.owner, name="Nails", price="5000", cost="3000", stock="50")
        sale = sales.create_sale(business=self.business, user=self.owner, items=[
            {"product_id": self.product.pk, "quantity": "1", "discount": "1000"},
            {"product_id": nails.pk, "quantity": "4"}], customer=self.credit_cust,
            payments=[{"method_id": self.cash.pk, "amount": "20000"}, {"method_id": self.momo.pk, "amount": "20000"}])
        # product: (30000-1000)=29000 + 18% = 34220 ; nails: 20000 untaxed
        self.assertEqual(sale.subtotal, D("50000.00"))
        self.assertEqual(sale.discount_total, D("1000.00"))
        self.assertEqual(sale.tax_total, D("5220.00"))
        self.assertEqual(sale.total, D("54220.00"))
        self.assertEqual(sale.amount_paid, D("40000.00"))
        self.assertEqual(sale.balance_due, D("14220.00"))
        self.assertEqual(sale.payment_status, "unpaid" if False else "partial")
        self.assertEqual(self.stock(nails), D("46"))

    def test_credit_sale_and_ledger_balance(self):
        sale = self.sell(paid="20000", customer=self.credit_cust)
        self.assertEqual(sale.balance_due, D("40000.00"))
        self.assertEqual(self.credit_cust.balance, D("40000.00"))
        self.assertIsNotNone(sale.due_date)
        entries = PartyLedgerEntry.objects.filter(customer=self.credit_cust)
        self.assertEqual([e.amount for e in entries], [D("60000.00"), D("-20000.00")])

    def test_credit_rules(self):
        with self.assertRaises(BusinessRuleError):
            self.sell(paid="0")  # credit without customer
        with self.assertRaises(BusinessRuleError):
            self.sell(paid="0", customer=self.cash_cust)
        with self.assertRaises(BusinessRuleError):
            self.sell(qty="4", paid="0", customer=self.credit_cust)  # 120k > 100k limit
        self.assertEqual(self.stock(), D("100"))  # failed sales leave no trace
        self.assertEqual(Sale.objects.count(), 0)

    def test_overpayment_and_insufficient_stock_roll_back(self):
        with self.assertRaises(BusinessRuleError):
            self.sell(paid="70000")
        with self.assertRaises(InsufficientStock):
            self.sell(qty="101", paid="3030000")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_partial_failure_rolls_back_all_items(self):
        nails = make_product(self.business, self.owner, name="Nails", stock="1")
        with self.assertRaises(InsufficientStock):
            sales.create_sale(business=self.business, user=self.owner, items=[
                {"product_id": self.product.pk, "quantity": "1"}, {"product_id": nails.pk, "quantity": "2"}],
                payments=[], customer=self.credit_cust)
        self.assertEqual(self.stock(), D("100"))
        self.assertEqual(self.stock(nails), D("1"))

    def test_invalid_input(self):
        for bad in ("0", "-1", "abc"):
            with self.assertRaises(BusinessRuleError):
                self.sell(qty=bad, paid="")
        with self.assertRaises(BusinessRuleError):
            sales.create_sale(business=self.business, user=self.owner, items=[], payments=[])

    def test_numbers_unique_and_sequential(self):
        a, b = self.sell(), self.sell()
        self.assertNotEqual(a.number, b.number)
        self.assertNotEqual(a.invoice.number, b.invoice.number)
        self.assertTrue(b.invoice.number.endswith("00002"))

    def test_customer_payment_allocates_oldest_first(self):
        s1 = self.sell(qty="1", paid="0", customer=self.credit_cust)
        s2 = self.sell(qty="1", paid="0", customer=self.credit_cust)
        receipt, payments = pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust,
                                                         amount=D("40000"), method=self.momo)
        s1.refresh_from_db(); s2.refresh_from_db()
        self.assertEqual((s1.payment_status, s1.balance_due), ("paid", D("0")))
        self.assertEqual((s2.payment_status, s2.balance_due), ("partial", D("20000.00")))
        self.assertEqual(self.credit_cust.balance, D("20000.00"))
        self.assertEqual(receipt.amount, D("40000.00"))
        self.assertEqual(len(payments), 2)

    def test_overpaying_customer_account_creates_credit(self):
        self.sell(qty="1", paid="0", customer=self.credit_cust)
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust, amount=D("35000"), method=self.cash)
        self.assertEqual(self.credit_cust.balance, D("-5000.00"))

    def test_payment_to_sale_cannot_exceed_balance(self):
        s = self.sell(qty="1", paid="10000", customer=self.credit_cust)
        with self.assertRaises(BusinessRuleError):
            pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust, sale=s, amount=D("25000"), method=self.cash)
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust, sale=s, amount=D("20000"), method=self.cash)
        s.refresh_from_db()
        self.assertEqual(s.payment_status, "paid")

    def test_reverse_payment_restores_balance(self):
        s = self.sell(qty="1", paid="0", customer=self.credit_cust)
        _, payments = pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust, amount=D("30000"), method=self.cash)
        self.assertEqual(self.credit_cust.balance, D("0"))
        pay.reverse_payment(payments[0], self.owner, "Bounced")
        s.refresh_from_db()
        self.assertEqual((s.balance_due, s.payment_status), (D("30000.00"), "unpaid"))
        self.assertEqual(self.credit_cust.balance, D("30000.00"))
        with self.assertRaises(BusinessRuleError):
            pay.reverse_payment(payments[0], self.owner, "again")

    def test_cancel_sale_restores_everything(self):
        s = self.sell(paid="20000", customer=self.credit_cust)
        sales.cancel_sale(s, self.owner, "Customer changed mind")
        s.refresh_from_db()
        self.assertEqual((s.status, s.payment_status, s.balance_due), ("cancelled", "void", D("0")))
        self.assertEqual(self.stock(), D("100"))
        self.assertEqual(self.credit_cust.balance, D("0"))
        self.assertEqual(Payment.objects.get().status, "reversed")
        self.assertEqual(s.invoice.status, "void")
        with self.assertRaises(BusinessRuleError):
            sales.cancel_sale(s, self.owner, "again")
        # nothing was deleted: full audit trail remains
        self.assertEqual(self.product.movements.filter(movement_type="sale_cancel").count(), 1)

    def test_return_with_cash_refund(self):
        s = self.sell(qty="3", paid="90000")
        item = s.items.get()
        ret = sales.create_sale_return(sale=s, user=self.owner, lines=[{"item_id": item.pk, "quantity": "1"}],
                                       reason="Wrong size", refund_amount=D("30000"), refund_method=self.cash)
        s.refresh_from_db()
        self.assertEqual(ret.total, D("30000.00"))
        self.assertEqual(self.stock(), D("98"))  # 100 - 3 + 1
        self.assertEqual((s.returned_total, s.amount_paid, s.balance_due, s.payment_status), (D("30000.00"), D("60000.00"), D("0"), "paid"))
        with self.assertRaises(BusinessRuleError):  # only 2 left to return
            sales.create_sale_return(sale=s, user=self.owner, lines=[{"item_id": item.pk, "quantity": "3"}], reason="x")
        with self.assertRaises(BusinessRuleError):
            sales.cancel_sale(s, self.owner, "no")

    def test_return_on_credit_sale_reduces_balance(self):
        s = self.sell(qty="2", paid="0", customer=self.credit_cust)
        sales.create_sale_return(sale=s, user=self.owner, lines=[{"item_id": s.items.get().pk, "quantity": "1"}], reason="Damaged")
        s.refresh_from_db()
        self.assertEqual(s.balance_due, D("30000.00"))
        self.assertEqual(self.credit_cust.balance, D("30000.00"))

    def test_customer_statement(self):
        self.sell(qty="1", paid="10000", customer=self.credit_cust)
        pay.receive_customer_payment(business=self.business, user=self.owner, customer=self.credit_cust, amount=D("5000"), method=self.cash)
        st = pay.customer_statement(self.credit_cust, date(2000, 1, 1), date(2100, 1, 1))
        self.assertEqual(st["opening"], D("0"))
        self.assertEqual([r for _, r in st["rows"]], [D("30000.00"), D("20000.00"), D("15000.00")])
        self.assertEqual(st["closing"], self.credit_cust.balance)

    def test_pos_json_endpoint_and_documents(self):
        self.login(self.owner)
        body = {"customer_id": "", "items": [{"product_id": self.product.pk, "quantity": "1", "unit_price": "30000", "discount": "0"}],
                "payments": [{"method_id": self.cash.pk, "amount": "30000"}]}
        r = self.client.post(reverse("sales:create"), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        sale = Sale.objects.get()
        self.assertEqual(r.json()["redirect"], f"/sales/{sale.pk}/")
        bad = self.client.post(reverse("sales:create"), json.dumps({"items": []}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("error", bad.json())
        self.assertEqual(self.client.get(reverse("sales:create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sales:detail", args=[sale.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("sales:list"), {"q": sale.number}).status_code, 200)
        pdf = self.client.get(reverse("invoices:invoice_pdf", args=[sale.invoice.pk]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        receipt = Receipt.objects.get()
        self.assertTrue(self.client.get(reverse("invoices:receipt_pdf", args=[receipt.pk])).content.startswith(b"%PDF"))
        for name, args in [("invoices:invoice_detail", [sale.invoice.pk]), ("invoices:receipt_detail", [receipt.pk]), ("invoices:list", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_sale_detail_return_and_cancel_views(self):
        s = self.sell(qty="2", paid="60000")
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("sales:return", args=[s.pk])).status_code, 200)
        r = self.client.post(reverse("sales:return", args=[s.pk]), {f"qty_{s.items.get().pk}": "1", "reason": "Faulty", "refund_amount": "30000", "method": self.cash.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.stock(), D("99"))

    def test_statement_pdf(self):
        self.sell(qty="1", paid="0", customer=self.credit_cust)
        self.login(self.owner)
        r = self.client.get(reverse("customers:statement_pdf", args=[self.credit_cust.pk]))
        self.assertTrue(r.content.startswith(b"%PDF"))


class PurchaseTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(business=self.business, name="Hima Cement")
        self.nails = make_product(self.business, self.owner, name="Nails", price="5000", cost="3000", stock="0")

    def buy(self, qty="10", cost="3200", receive_now=False):
        return pur.create_purchase(business=self.business, user=self.owner, supplier=self.supplier,
                                   items=[{"product_id": self.nails.pk, "quantity": qty, "unit_cost": cost}], receive_now=receive_now)

    def test_order_does_not_touch_stock_or_balance(self):
        po = self.buy()
        self.assertEqual((po.status, po.total), ("ordered", D("32000.00")))
        self.assertEqual(self.stock(self.nails), D("0"))
        self.assertEqual(self.supplier.balance, D("0"))

    def test_partial_deliveries_update_stock_and_supplier_balance(self):
        po = self.buy()
        item = po.items.get()
        pur.receive_goods(po, self.owner, [{"item_id": item.pk, "quantity": "4"}])
        po.refresh_from_db()
        self.assertEqual((po.status, po.received_value), ("partially_received", D("12800.00")))
        self.assertEqual(self.stock(self.nails), D("4"))
        self.assertEqual(self.supplier.balance, D("12800.00"))
        with self.assertRaises(BusinessRuleError):
            pur.receive_goods(po, self.owner, [{"item_id": item.pk, "quantity": "7"}])
        pur.receive_goods(po, self.owner, [{"item_id": item.pk, "quantity": "6"}])
        po.refresh_from_db()
        self.assertEqual(po.status, "received")
        self.assertEqual(self.stock(self.nails), D("10"))
        self.assertEqual(self.supplier.balance, D("32000.00"))
        self.assertEqual(self.nails.movements.filter(movement_type="purchase").count(), 2)
        with self.assertRaises(BusinessRuleError):
            pur.receive_goods(po, self.owner, [{"item_id": item.pk, "quantity": "1"}])

    def test_moving_average_cost(self):
        stocked = make_product(self.business, self.owner, name="Pipes", cost="1000", stock="10")
        po = pur.create_purchase(business=self.business, user=self.owner, supplier=self.supplier,
                                 items=[{"product_id": stocked.pk, "quantity": "10", "unit_cost": "2000"}], receive_now=True)
        stocked.refresh_from_db()
        self.assertEqual(stocked.purchase_price, D("1500.00"))

    def test_supplier_payment_and_balance(self):
        po = self.buy(receive_now=True)
        pay.pay_supplier(business=self.business, user=self.owner, supplier=self.supplier, amount=D("20000"), method=self.cash, purchase=po)
        po.refresh_from_db()
        self.assertEqual(po.amount_paid, D("20000.00"))
        self.assertEqual(po.balance_due, D("12000.00"))
        self.assertEqual(self.supplier.balance, D("12000.00"))
        p = Payment.objects.get()
        pay.reverse_payment(p, self.owner, "Wrong amount")
        po.refresh_from_db()
        self.assertEqual(self.supplier.balance, D("32000.00"))
        self.assertEqual(po.amount_paid, D("0"))

    def test_purchase_return(self):
        po = self.buy(receive_now=True)
        ret = pur.return_goods(po, self.owner, [{"item_id": po.items.get().pk, "quantity": "3"}], "Defective")
        self.assertEqual(ret.total, D("9600.00"))
        self.assertEqual(self.stock(self.nails), D("7"))
        self.assertEqual(self.supplier.balance, D("22400.00"))
        with self.assertRaises(BusinessRuleError):
            pur.return_goods(po, self.owner, [{"item_id": po.items.get().pk, "quantity": "8"}], "too many")

    def test_cancel_rules(self):
        po = self.buy()
        pur.cancel_purchase(po, self.owner)
        with self.assertRaises(BusinessRuleError):
            pur.receive_goods(po, self.owner, [{"item_id": po.items.get().pk, "quantity": "1"}])
        po2 = self.buy(receive_now=True)
        with self.assertRaises(BusinessRuleError):
            pur.cancel_purchase(po2, self.owner)

    def test_cannot_buy_from_other_business_supplier(self):
        foreign = Supplier.objects.create(business=self.other, name="Foreign")
        with self.assertRaises(Supplier.DoesNotExist):
            pur.create_purchase(business=self.business, user=self.owner, supplier=foreign,
                                items=[{"product_id": self.nails.pk, "quantity": "1", "unit_cost": "1"}])

    def test_purchase_views(self):
        self.login(self.owner)
        body = {"supplier_id": self.supplier.pk, "receive_now": True,
                "items": [{"product_id": self.nails.pk, "quantity": "5", "unit_cost": "3000"}]}
        r = self.client.post(reverse("purchases:create"), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        po = PurchaseOrder.objects.get()
        self.assertEqual(self.stock(self.nails), D("5"))
        for name in ["purchases:list", "purchases:create"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ["purchases:detail", "purchases:return"]:
            self.assertEqual(self.client.get(reverse(name, args=[po.pk])).status_code, 200)
        po2 = self.buy()
        self.assertEqual(self.client.get(reverse("purchases:receive", args=[po2.pk])).status_code, 200)
        r = self.client.post(reverse("purchases:receive", args=[po2.pk]), {f"qty_{po2.items.get().pk}": "10"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.stock(self.nails), D("15"))
        self.assertEqual(self.client.get(reverse("suppliers:detail", args=[self.supplier.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("suppliers:statement_pdf", args=[self.supplier.pk])).status_code, 200)

    def test_stock_manager_can_receive_but_not_pay(self):
        sm = make_user(self.business, "stock_manager", "stella@alpha.test")
        self.login(sm)
        self.assertEqual(self.client.get(reverse("purchases:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("payments:supplier_payment")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:summary")).status_code, 403)
