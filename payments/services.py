from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import log_action
from contacts.models import Customer, Supplier
from core.exceptions import BusinessRuleError
from core.utils import q2

from .models import Payment, PartyLedgerEntry, PaymentMethod

L = PartyLedgerEntry


def post_ledger(*, business, user, amount, entry_type, description, customer=None, supplier=None,
                entry_date=None, source_type="", source_id=None):
    amount = q2(amount)
    if amount == 0:
        return None
    return PartyLedgerEntry.objects.create(
        business=business, customer=customer, supplier=supplier, entry_type=entry_type, amount=amount,
        entry_date=entry_date or timezone.localdate(), description=description[:200],
        source_type=source_type, source_id=source_id, created_by=user,
    )


def customer_balance(customer):
    return customer.balance


@transaction.atomic
def post_payment(*, business, user, direction, purpose, method, amount, customer=None, supplier=None,
                 sale=None, purchase=None, paid_on=None, reference="", notes=""):
    """Create one Payment row and its ledger entry (if a customer/supplier is involved)."""
    amount = q2(amount)
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    if method.business_id != business.id or not method.is_active:
        raise BusinessRuleError("Choose an active payment method.")
    payment = Payment.objects.create(
        business=business, direction=direction, purpose=purpose, customer=customer, supplier=supplier,
        sale=sale, purchase=purchase, method=method, amount=amount, paid_on=paid_on or timezone.localdate(),
        reference=reference[:80], notes=notes[:300], created_by=user,
    )
    if customer is not None:
        if direction == Payment.IN:
            post_ledger(business=business, user=user, amount=-amount, entry_type=L.PAYMENT, customer=customer,
                        description=f"Payment received ({method.name})", entry_date=payment.paid_on,
                        source_type="payment", source_id=payment.pk)
        else:
            post_ledger(business=business, user=user, amount=amount, entry_type=L.REFUND, customer=customer,
                        description=f"Refund paid ({method.name})", entry_date=payment.paid_on,
                        source_type="payment", source_id=payment.pk)
    if supplier is not None:
        post_ledger(business=business, user=user, amount=-amount, entry_type=L.PAYMENT, supplier=supplier,
                    description=f"Payment to supplier ({method.name})", entry_date=payment.paid_on,
                    source_type="payment", source_id=payment.pk)
    log_action("payment.create", business=business, user=user, obj=payment,
               after={"amount": amount, "direction": direction, "purpose": purpose})
    return payment


@transaction.atomic
def receive_customer_payment(*, business, user, customer, amount, method, sale=None, paid_on=None,
                             reference="", notes=""):
    """Receive money from a customer. Allocates to ``sale`` or, otherwise, oldest unpaid sales first."""
    from invoices.services import issue_receipt
    from sales.models import Sale

    amount = q2(amount)
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    customer = Customer.objects.select_for_update().get(pk=customer.pk, business=business)
    if sale is not None:
        sale = Sale.objects.select_for_update().get(pk=sale.pk, business=business)
        if sale.status != Sale.COMPLETED:
            raise BusinessRuleError("Payments cannot be added to a cancelled sale.")
        if amount > sale.balance_due:
            raise BusinessRuleError(f"This sale only has {sale.balance_due:,.2f} outstanding.")
        targets = [(sale, amount)]
        leftover = Decimal("0")
    else:
        targets, remaining = [], amount
        open_sales = Sale.objects.select_for_update().filter(
            business=business, customer=customer, status=Sale.COMPLETED, balance_due__gt=0
        ).order_by("sale_date", "id")
        for s in open_sales:
            if remaining <= 0:
                break
            part = min(remaining, s.balance_due)
            targets.append((s, part))
            remaining -= part
        leftover = remaining
    payments = []
    for s, part in targets:
        payments.append(post_payment(business=business, user=user, direction=Payment.IN, purpose=Payment.CUSTOMER_PAYMENT,
                                     method=method, amount=part, customer=customer, sale=s, paid_on=paid_on,
                                     reference=reference, notes=notes))
    if leftover > 0:
        payments.append(post_payment(business=business, user=user, direction=Payment.IN, purpose=Payment.CUSTOMER_PAYMENT,
                                     method=method, amount=leftover, customer=customer, paid_on=paid_on,
                                     reference=reference, notes=notes or "Payment on account"))
    for s, _ in targets:
        s.refresh_payment_state()
    receipt = issue_receipt(business=business, user=user, payments=payments, customer=customer)
    return receipt, payments


@transaction.atomic
def pay_supplier(*, business, user, supplier, amount, method, purchase=None, paid_on=None, reference="", notes=""):
    supplier = Supplier.objects.select_for_update().get(pk=supplier.pk, business=business)
    if purchase is not None and (purchase.business_id != business.id or purchase.supplier_id != supplier.pk):
        raise BusinessRuleError("That purchase does not belong to this supplier.")
    payment = post_payment(business=business, user=user, direction=Payment.OUT, purpose=Payment.SUPPLIER_PAYMENT,
                           method=method, amount=amount, supplier=supplier, purchase=purchase, paid_on=paid_on,
                           reference=reference, notes=notes)
    if purchase is not None:
        purchase.refresh_totals()
    return payment


@transaction.atomic
def reverse_payment(payment, user, reason):
    payment = Payment.objects.select_for_update().get(pk=payment.pk, business=user.business)
    if payment.status != Payment.POSTED:
        raise BusinessRuleError("This payment has already been reversed.")
    if not reason.strip():
        raise BusinessRuleError("Please give a reason for the reversal.")
    payment.status = Payment.REVERSED
    payment.reversed_by, payment.reversed_at, payment.reverse_reason = user, timezone.now(), reason[:300]
    payment.save()
    sign = 1 if payment.direction == Payment.IN else -1  # undo the original ledger effect
    for party in ("customer", "supplier"):
        obj = getattr(payment, party)
        if obj is not None:
            if party == "customer":
                amount = payment.amount * sign
            else:
                amount = payment.amount  # supplier payments reduced the balance; reversing raises it
            post_ledger(business=payment.business, user=user, amount=amount, entry_type=L.REVERSAL,
                        description=f"Reversal of payment #{payment.pk}: {reason}", source_type="payment",
                        source_id=payment.pk, **{party: obj})
    if payment.sale_id:
        payment.sale.refresh_payment_state()
    if payment.purchase_id:
        payment.purchase.refresh_totals()
    log_action("payment.reverse", business=payment.business, user=user, obj=payment, message=reason)
    return payment


def customer_statement(customer, start, end):
    """Return dict(opening, entries[(entry, running)], closing) for a date range."""
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    return _statement(PartyLedgerEntry.objects.filter(customer=customer), start, end)


def supplier_statement(supplier, start, end):
    return _statement(PartyLedgerEntry.objects.filter(supplier=supplier), start, end)


def _statement(qs, start, end):
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    opening = qs.filter(entry_date__lt=start).aggregate(t=Coalesce(Sum("amount"), Decimal("0")))["t"]
    running, rows = opening, []
    for e in qs.filter(entry_date__gte=start, entry_date__lte=end).order_by("entry_date", "id"):
        running += e.amount
        rows.append((e, running))
    return {"opening": opening, "rows": rows, "closing": running}
