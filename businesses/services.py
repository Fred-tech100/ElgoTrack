from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.services import create_default_roles
from audit.services import log_action
from core.exceptions import BusinessRuleError

from .models import Business, PlatformBillingSequence, PlatformInvoice, PlatformInvoiceItem, PlatformPayment

DEFAULT_UNITS = [("Piece", "pc"), ("Kilogram", "kg"), ("Gram", "g"), ("Litre", "L"), ("Metre", "m"),
                 ("Bag", "bag"), ("Box", "box"), ("Pack", "pack"), ("Dozen", "doz"), ("Carton", "ctn")]
DEFAULT_METHODS = ["Cash", "Mobile Money", "Bank", "Card"]
DEFAULT_EXPENSE_CATEGORIES = ["Rent", "Electricity", "Transport", "Salaries", "Airtime", "Internet",
                              "Repairs", "Packaging", "Other"]


def provision_defaults(business):
    """Seed roles, units, payment methods and expense categories for a new business."""
    from expenses.models import ExpenseCategory
    from payments.models import PaymentMethod
    from products.models import Unit

    roles = create_default_roles(business)
    Unit.objects.bulk_create(Unit(business=business, name=n, abbreviation=a) for n, a in DEFAULT_UNITS)
    PaymentMethod.objects.bulk_create(
        PaymentMethod(business=business, name=n, sort_order=i) for i, n in enumerate(DEFAULT_METHODS)
    )
    ExpenseCategory.objects.bulk_create(ExpenseCategory(business=business, name=n) for n in DEFAULT_EXPENSE_CATEGORIES)
    return roles


@transaction.atomic
def create_business(*, name, business_type, owner_email, owner_name, owner_password=None,
                    owner_phone="", status=Business.ACTIVE, **extra):
    """Create a business with its defaults and owner. Returns (business, owner)."""
    extra.setdefault("phone", owner_phone)
    business = Business.objects.create(name=name, business_type=business_type, status=status, **extra)
    roles = provision_defaults(business)
    owner = User.objects.create_user(
        email=owner_email, password=owner_password, full_name=owner_name, phone=owner_phone,
        business=business, role=roles["owner"]
    )
    log_action("business.create", business=business, obj=business, after={"status": status, "owner": owner_email})
    return business, owner


def _next_platform_number(key):
    year = timezone.localdate().year
    sequence, _ = PlatformBillingSequence.objects.select_for_update().get_or_create(key=key, year=year)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    prefix = "ENI" if key == "invoice" else "ENR"
    return f"{prefix}-{year}-{sequence.last_number:05d}"


@transaction.atomic
def create_platform_invoice(*, user, client_business, client_name, client_email="", client_phone="",
                            client_address="", currency_code="UGX", currency_decimals=0, issue_date,
                            due_date=None, notes="", terms="", items):
    if not items:
        raise BusinessRuleError("An invoice must include at least one line item.")
    if client_business:
        client_name = client_name or client_business.name
        client_email = client_email or client_business.email
        client_phone = client_phone or client_business.phone
        client_address = client_address or client_business.address

    invoice = PlatformInvoice.objects.create(
        number=_next_platform_number("invoice"), business=client_business, client_name=client_name,
        client_email=client_email, client_phone=client_phone, client_address=client_address,
        currency_code=currency_code.upper(), currency_decimals=currency_decimals, issue_date=issue_date,
        due_date=due_date, notes=notes, terms=terms, created_by=user,
    )
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for item in items:
        net = (item["quantity"] * item["unit_price"]).quantize(Decimal("0.01"))
        tax = (net * item["tax_rate"] / Decimal("100")).quantize(Decimal("0.01"))
        if net + tax > Decimal("999999999999.99"):
            raise BusinessRuleError("An invoice line exceeds the supported amount.")
        PlatformInvoiceItem.objects.create(
            invoice=invoice, description=item["description"], quantity=item["quantity"],
            unit_price=item["unit_price"], tax_rate=item["tax_rate"], tax_amount=tax, line_total=net + tax,
        )
        subtotal += net
        tax_total += tax
        if subtotal + tax_total > Decimal("999999999999.99"):
            raise BusinessRuleError("The invoice total exceeds the supported amount.")
    invoice.subtotal = subtotal
    invoice.tax_total = tax_total
    invoice.total = subtotal + tax_total
    from invoices import verification

    invoice.signature = verification.compute_signature(invoice)
    invoice.save(update_fields=["subtotal", "tax_total", "total", "signature", "updated_at"])
    log_action("platform.invoice_issued", user=user, obj=None, message=f"{invoice.number} issued to {invoice.client_name}")
    return invoice


@transaction.atomic
def record_platform_payment(*, invoice, user, amount, paid_on, method, reference="", notes=""):
    invoice = PlatformInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != PlatformInvoice.ISSUED:
        raise BusinessRuleError("Payments cannot be recorded against a void invoice.")
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    if amount > invoice.balance_due:
        raise BusinessRuleError("Payment cannot exceed the invoice balance due.")
    payment = PlatformPayment.objects.create(
        invoice=invoice, number=_next_platform_number("receipt"), amount=amount,
        balance_after=invoice.balance_due - amount, paid_on=paid_on,
        method=method, reference=reference, notes=notes, received_by=user,
    )
    invoice.amount_paid += amount
    invoice.save(update_fields=["amount_paid", "updated_at"])
    from invoices import verification

    payment.refresh_from_db()
    payment.signature = verification.compute_signature(payment)
    payment.save(update_fields=["signature"])
    log_action("platform.payment_received", user=user, obj=None,
               message=f"{payment.number}: {amount} {invoice.currency_code} received for {invoice.number}")
    return payment


def set_status(business, status, user=None):
    before = business.status
    business.status = status
    business.save(update_fields=["status", "updated_at"])
    log_action(f"business.{status}", business=business, user=user, obj=business, before={"status": before}, after={"status": status})
