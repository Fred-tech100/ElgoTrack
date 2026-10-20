from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import log_action
from contacts.models import Customer
from core.exceptions import BusinessRuleError
from core.models import next_number
from core.utils import D, q2, q3
from inventory.models import StockMovement
from inventory.services import record_movement
from payments import services as pay
from payments.models import Payment, PaymentMethod, PartyLedgerEntry
from products.models import Product

from .models import Sale, SaleItem, SaleReturn, SaleReturnItem

ZERO = Decimal("0")


@transaction.atomic
def create_sale(*, business, user, items, customer=None, payments=(), due_date=None, notes="", sale_date=None, client_token=""):
    """Create a completed sale atomically.

    items:    [{"product_id", "quantity", "unit_price"(optional), "discount"(optional, line total)}]
    payments: [{"method_id", "amount", "reference"}]
    Stock, customer ledger, payments, invoice and receipt are all written in one transaction.
    """
    from invoices.services import issue_invoice, issue_receipt

    if client_token:
        existing = Sale.objects.filter(business=business, client_token=client_token).first()
        if existing:
            return existing
    if not items:
        raise BusinessRuleError("Add at least one product to the sale.")
    merged = {}
    for raw in items:
        pid = int(raw["product_id"])
        merged.setdefault(pid, []).append(raw)
    products = {p.pk: p for p in Product.objects.filter(business=business, pk__in=merged.keys())}
    if len(products) != len(merged):
        raise BusinessRuleError("One or more products could not be found.")

    if customer is not None:
        customer = Customer.objects.select_for_update().get(pk=customer.pk, business=business)
        if not customer.is_active:
            raise BusinessRuleError("This customer is inactive.")

    can_discount = user.has_business_perm("sales.discount")
    lines, subtotal, discount_total, tax_total = [], ZERO, ZERO, ZERO
    for pid in sorted(merged):  # consistent lock order avoids deadlocks
        product = products[pid]
        if not product.is_active:
            raise BusinessRuleError(f"{product.display_name} is inactive and cannot be sold.")
        for raw in merged[pid]:
            qty = q3(D(raw.get("quantity"), ZERO))
            if qty <= 0:
                raise BusinessRuleError(f"Enter a quantity greater than zero for {product.display_name}.")
            price = q2(D(raw.get("unit_price"), product.selling_price))
            discount = q2(D(raw.get("discount"), ZERO))
            allowed_prices = {product.selling_price, product.wholesale_price}
            if (price not in allowed_prices or discount > 0) and not can_discount:
                raise BusinessRuleError("You do not have permission to change prices or give discounts.")
            if price < 0 or discount < 0:
                raise BusinessRuleError("Price and discount cannot be negative.")
            gross = q2(qty * price)
            if discount > gross:
                raise BusinessRuleError(f"Discount for {product.display_name} exceeds the line amount.")
            rate = Decimal(product.effective_tax_rate())
            tax = q2((gross - discount) * rate / 100)
            lines.append(dict(product=product, qty=qty, price=price, discount=discount, rate=rate, tax=tax,
                              total=gross - discount + tax))
            subtotal += gross
            discount_total += discount
            tax_total += tax
    total = subtotal - discount_total + tax_total

    tenders = []
    paid = ZERO
    for p in payments:
        amount = q2(D(p.get("amount"), ZERO))
        if amount <= 0:
            continue
        method = PaymentMethod.objects.filter(business=business, pk=p.get("method_id"), is_active=True).first()
        if method is None:
            raise BusinessRuleError("Choose a valid payment method.")
        tenders.append((method, amount, p.get("reference", "")))
        paid += amount
    if paid > total:
        raise BusinessRuleError("Payments are more than the sale total.")
    credit = total - paid
    if credit > 0:
        if customer is None:
            raise BusinessRuleError("Choose a customer to sell on credit, or take full payment.")
        if customer.customer_type != Customer.CREDIT:
            raise BusinessRuleError(f"{customer.name} is a cash customer and cannot buy on credit.")
        if customer.credit_limit > 0 and customer.balance + credit > customer.credit_limit:
            raise BusinessRuleError(
                f"This sale would take {customer.name} over their credit limit of {customer.credit_limit:,.2f}.")
        due_date = due_date or (timezone.localdate() + timedelta(days=business.default_credit_days))
    else:
        due_date = None

    sale = Sale.objects.create(
        business=business, number=next_number(business, "sale", "SAL"), customer=customer,
        client_token=client_token[:64], sale_date=sale_date or timezone.now(), subtotal=subtotal, discount_total=discount_total,
        tax_total=tax_total, total=total, due_date=due_date, notes=notes[:300], created_by=user,
    )
    for ln in lines:
        p = ln["product"]
        SaleItem.objects.create(
            sale=sale, product=p, description=p.display_name, quantity=ln["qty"], unit_price=ln["price"],
            unit_cost=p.purchase_price, discount=ln["discount"], tax_rate=ln["rate"], tax_amount=ln["tax"],
            line_total=ln["total"],
        )
        record_movement(
            business=business, product=p, movement_type=StockMovement.SALE, quantity=-ln["qty"], user=user,
            unit_cost=p.purchase_price, source_type="sale", source_id=sale.pk, source_ref=sale.number,
        )
    if customer is not None:
        pay.post_ledger(business=business, user=user, amount=total, entry_type=PartyLedgerEntry.SALE,
                        customer=customer, description=f"Sale {sale.number}", entry_date=timezone.localdate(),
                        source_type="sale", source_id=sale.pk)
    posted = [
        pay.post_payment(business=business, user=user, direction=Payment.IN, purpose=Payment.CUSTOMER_PAYMENT,
                         method=m, amount=a, customer=customer, sale=sale, reference=ref)
        for m, a, ref in tenders
    ]
    sale.refresh_payment_state()
    issue_invoice(sale, user)
    if posted:
        issue_receipt(business=business, user=user, payments=posted, customer=customer)
    log_action("sale.create", business=business, user=user, obj=sale, after={"total": total, "paid": paid})
    return sale


@transaction.atomic
def cancel_sale(sale, user, reason):
    sale = Sale.objects.select_for_update().get(pk=sale.pk, business=user.business)
    if sale.status != Sale.COMPLETED:
        raise BusinessRuleError("This sale is already cancelled.")
    if not reason.strip():
        raise BusinessRuleError("Please give a reason for cancelling the sale.")
    if sale.returns.exists():
        raise BusinessRuleError("A sale with returns cannot be cancelled.")
    for item in sale.items.select_related("product").order_by("product_id"):
        record_movement(
            business=sale.business, product=item.product, movement_type=StockMovement.SALE_CANCEL,
            quantity=item.quantity, user=user, unit_cost=item.unit_cost, reason=f"Cancelled: {reason}",
            source_type="sale", source_id=sale.pk, source_ref=sale.number,
        )
    for payment in sale.payments.filter(status=Payment.POSTED):
        pay.reverse_payment(payment, user, f"Sale {sale.number} cancelled")
    if sale.customer_id:
        pay.post_ledger(business=sale.business, user=user, amount=-sale.total, entry_type=PartyLedgerEntry.REVERSAL,
                        customer=sale.customer, description=f"Sale {sale.number} cancelled",
                        source_type="sale", source_id=sale.pk)
    sale.status = Sale.CANCELLED
    sale.cancelled_by, sale.cancelled_at, sale.cancel_reason = user, timezone.now(), reason[:300]
    sale.save()
    sale.refresh_payment_state()
    if hasattr(sale, "invoice"):
        sale.invoice.status = "void"
        sale.invoice.save(update_fields=["status"])
    log_action("sale.cancel", business=sale.business, user=user, obj=sale, message=reason)
    return sale


@transaction.atomic
def create_sale_return(*, sale, user, lines, reason, refund_amount=0, refund_method=None):
    """lines: [{"item_id", "quantity"}]. Restocks, reduces what the customer owes, optionally refunds cash."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk, business=user.business)
    if sale.status != Sale.COMPLETED:
        raise BusinessRuleError("Returns can only be made on completed sales.")
    if not reason.strip():
        raise BusinessRuleError("Please give a reason for the return.")
    items = {i.pk: i for i in sale.items.select_related("product")}
    chosen = []
    for ln in lines:
        qty = q3(D(ln.get("quantity"), ZERO))
        if qty <= 0:
            continue
        item = items.get(int(ln["item_id"]))
        if item is None:
            raise BusinessRuleError("Unknown sale item.")
        available = item.quantity - item.returned_quantity()
        if qty > available:
            raise BusinessRuleError(f"Only {available.normalize():f} of {item.description} can still be returned.")
        chosen.append((item, qty))
    if not chosen:
        raise BusinessRuleError("Enter a quantity for at least one item to return.")
    ret = SaleReturn.objects.create(business=sale.business, number=next_number(sale.business, "return", "RET"),
                                    sale=sale, reason=reason[:300], created_by=user)
    net_total = tax_total = ZERO
    for item, qty in sorted(chosen, key=lambda t: t[0].product_id):
        ratio = qty / item.quantity
        net = q2(item.net_amount * ratio)
        tax = q2(item.tax_amount * ratio)
        SaleReturnItem.objects.create(sale_return=ret, sale_item=item, quantity=qty, net_amount=net,
                                      tax_amount=tax, amount=net + tax)
        net_total += net
        tax_total += tax
        record_movement(
            business=sale.business, product=item.product, movement_type=StockMovement.SALE_RETURN, quantity=qty,
            user=user, unit_cost=item.unit_cost, reason=reason, source_type="sale_return", source_id=ret.pk,
            source_ref=ret.number,
        )
    ret.net_total, ret.tax_total, ret.total = net_total, tax_total, net_total + tax_total
    refund_amount = q2(D(refund_amount, ZERO))
    if refund_amount > ret.total:
        raise BusinessRuleError("The refund cannot be more than the value of the returned goods.")
    ret.refund_amount = refund_amount
    ret.save()
    if sale.customer_id:
        pay.post_ledger(business=sale.business, user=user, amount=-ret.total, entry_type=PartyLedgerEntry.RETURN,
                        customer=sale.customer, description=f"Return {ret.number} on {sale.number}",
                        source_type="sale_return", source_id=ret.pk)
    if refund_amount > 0:
        if refund_method is None:
            raise BusinessRuleError("Choose how the refund is paid.")
        pay.post_payment(business=sale.business, user=user, direction=Payment.OUT, purpose=Payment.SALE_REFUND,
                         method=refund_method, amount=refund_amount, customer=sale.customer, sale=sale,
                         notes=f"Refund for {ret.number}")
    sale.refresh_payment_state()
    log_action("sale.return", business=sale.business, user=user, obj=sale, after={"return": ret.number, "total": ret.total})
    return ret
