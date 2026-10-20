from django.db import transaction
from django.db.models import Sum

from core.models import next_number

from .models import Invoice, Receipt


@transaction.atomic
def issue_invoice(sale, user):
    b = sale.business
    return Invoice.objects.create(
        business=b, sale=sale, number=next_number(b, "invoice", b.invoice_prefix or "INV"),
        issue_date=sale.sale_date.date(), due_date=sale.due_date, terms=b.invoice_terms,
    )


@transaction.atomic
def issue_receipt(*, business, user, payments, customer=None):
    payments = [p for p in payments if p is not None]
    total = sum(p.amount for p in payments)
    receipt = Receipt.objects.create(
        business=business, customer=customer, amount=total, issued_by=user,
        number=next_number(business, "receipt", business.receipt_prefix or "RCT"),
    )
    for p in payments:
        p.receipt = receipt
        p.save(update_fields=["receipt", "updated_at"])
    return receipt
