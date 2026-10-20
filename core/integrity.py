"""Independent consistency checks between source records and cached/derived figures.

Run with ``python manage.py check_integrity`` or nightly via Celery. Returns human-readable problems; an empty
list means every cached stock level, sale balance and customer/supplier balance agrees with its source records.
"""
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

ZERO = Decimal("0")


def _s(qs, field):
    return qs.aggregate(t=Coalesce(Sum(field), ZERO))["t"]


def check_business(business):
    from contacts.models import Customer, Supplier
    from inventory.models import StockLevel, StockMovement
    from payments.models import Payment, PartyLedgerEntry
    from purchases.models import GoodsReceiptItem, PurchaseReturn
    from sales.models import Sale, SaleReturn

    problems = []
    # 1. stock level == sum of ledger
    for lv in StockLevel.objects.filter(business=business).select_related("product"):
        total = _s(StockMovement.objects.filter(product=lv.product), "quantity")
        if total != lv.quantity:
            problems.append(f"Stock mismatch for {lv.product}: level {lv.quantity} but ledger sums to {total}")
    # 2. cached sale payment state == recomputed from payments and returns
    for s in Sale.objects.filter(business=business, status=Sale.COMPLETED):
        posted = s.payments.filter(status=Payment.POSTED)
        paid = _s(posted.filter(direction=Payment.IN), "amount") - _s(posted.filter(direction=Payment.OUT), "amount")
        returned = _s(s.returns.all(), "total")
        balance = max(s.total - returned - paid, ZERO)
        if (paid, returned, balance) != (s.amount_paid, s.returned_total, s.balance_due):
            problems.append(f"Sale {s.number}: cached paid/returned/balance {s.amount_paid}/{s.returned_total}/{s.balance_due} "
                            f"differ from {paid}/{returned}/{balance}")
    # 3. customer ledger == independently derived balance
    manual = (PartyLedgerEntry.OPENING, PartyLedgerEntry.ADJUSTMENT)
    for c in Customer.objects.filter(business=business):
        ledger = _s(PartyLedgerEntry.objects.filter(customer=c), "amount")
        pay = Payment.objects.filter(customer=c, status=Payment.POSTED)
        expected = (_s(Sale.objects.filter(customer=c, status=Sale.COMPLETED), "total")
                    - _s(SaleReturn.objects.filter(sale__customer=c, sale__status=Sale.COMPLETED), "total")
                    - _s(pay.filter(direction=Payment.IN), "amount") + _s(pay.filter(direction=Payment.OUT), "amount")
                    + _s(PartyLedgerEntry.objects.filter(customer=c, entry_type__in=manual), "amount"))
        if ledger != expected:
            problems.append(f"Customer {c.name}: ledger balance {ledger} but sales/payments imply {expected}")
    # 4. supplier ledger == derived balance
    for sp in Supplier.objects.filter(business=business):
        ledger = _s(PartyLedgerEntry.objects.filter(supplier=sp), "amount")
        received = sum((i.quantity * i.unit_cost for i in GoodsReceiptItem.objects.filter(receipt__purchase__supplier=sp)), ZERO)
        returned = _s(PurchaseReturn.objects.filter(purchase__supplier=sp), "total")
        paid = _s(Payment.objects.filter(supplier=sp, status=Payment.POSTED), "amount")
        expected = (received - returned - paid + _s(PartyLedgerEntry.objects.filter(supplier=sp, entry_type__in=manual), "amount")).quantize(Decimal("0.01"))
        if ledger != expected:
            problems.append(f"Supplier {sp.name}: ledger balance {ledger} but receipts/payments imply {expected}")
    return problems


def check_all():
    from businesses.models import Business

    return {b: check_business(b) for b in Business.objects.all()}
