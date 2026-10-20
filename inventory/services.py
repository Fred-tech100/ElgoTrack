"""Inventory domain logic. Every stock change goes through ``record_movement``."""
from django.db import transaction
from django.utils import timezone

from audit.services import log_action
from core.exceptions import BusinessRuleError, InsufficientStock
from core.models import next_number
from core.utils import q3

from .models import StockCount, StockCountLine, StockLevel, StockMovement

MANUAL_TYPES = {
    StockMovement.OPENING: 1, StockMovement.ADJUSTMENT: 0, StockMovement.DAMAGE: -1, StockMovement.LOSS: -1,
}


@transaction.atomic
def record_movement(*, business, product, movement_type, quantity, user, unit_cost=None, reason="",
                    source_type="", source_id=None, source_ref="", batch_number="", expiry_date=None,
                    allow_negative=None):
    """Apply a signed quantity to a product's stock under a row lock and write the ledger entry."""
    quantity = q3(quantity)
    if quantity == 0:
        raise BusinessRuleError("Quantity cannot be zero.")
    if product.business_id != business.id:
        raise BusinessRuleError("Product does not belong to this business.")
    StockLevel.objects.get_or_create(product=product, defaults={"business": business})
    level = StockLevel.objects.select_for_update().get(product=product)
    new_balance = level.quantity + quantity
    if allow_negative is None:
        allow_negative = business.allow_negative_stock
    if new_balance < 0 and quantity < 0 and not allow_negative:
        raise InsufficientStock(
            f"Not enough stock for {product.display_name}: {level.quantity.normalize():f} available, "
            f"{abs(quantity).normalize():f} requested."
        )
    level.quantity = new_balance
    level.save(update_fields=["quantity", "updated_at"])
    return StockMovement.objects.create(
        business=business, product=product, movement_type=movement_type, quantity=quantity,
        balance_after=new_balance, unit_cost=unit_cost, reason=reason[:300], source_type=source_type,
        source_id=source_id, source_ref=source_ref, batch_number=batch_number, expiry_date=expiry_date,
        created_by=user,
    )


@transaction.atomic
def manual_adjustment(*, business, user, product, movement_type, quantity, reason="", unit_cost=None,
                      batch_number="", expiry_date=None):
    """Opening stock, adjustments, damage and loss entered by hand."""
    if movement_type not in MANUAL_TYPES:
        raise BusinessRuleError("Unsupported movement type.")
    quantity = q3(quantity)
    sign = MANUAL_TYPES[movement_type]
    if sign and quantity != 0:
        quantity = abs(quantity) * sign
    if movement_type != StockMovement.OPENING and not reason.strip():
        raise BusinessRuleError("Please give a reason for this stock change.")
    movement = record_movement(
        business=business, product=product, movement_type=movement_type, quantity=quantity, user=user,
        unit_cost=unit_cost if unit_cost is not None else product.purchase_price, reason=reason,
        source_type="manual", batch_number=batch_number, expiry_date=expiry_date,
    )
    log_action(f"stock.{movement_type}", business=business, user=user, obj=product,
               after={"quantity": quantity, "balance": movement.balance_after, "reason": reason})
    return movement


# --------------------------------------------------------------- stock counts
@transaction.atomic
def start_count(*, business, user, category=None, notes=""):
    from products.models import Product

    products = Product.objects.filter(business=business, is_active=True).select_related("stock")
    if category:
        products = products.filter(category=category)
    if not products.exists():
        raise BusinessRuleError("There are no active products to count.")
    count = StockCount.objects.create(
        business=business, number=next_number(business, "count", "CNT"), category=category, notes=notes, started_by=user
    )
    StockCountLine.objects.bulk_create(
        StockCountLine(count=count, product=p, expected_quantity=p.stock.quantity if hasattr(p, "stock") else 0,
                       unit_cost=p.purchase_price)
        for p in products
    )
    log_action("stock.count_start", business=business, user=user, obj=count)
    return count


@transaction.atomic
def save_count_entries(count, entries):
    """entries: {line_id: (counted_quantity_or_None, reason)}"""
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.DRAFT:
        raise BusinessRuleError("This count can no longer be edited.")
    lines = {l.pk: l for l in count.lines.all()}
    for line_id, (counted, reason) in entries.items():
        line = lines.get(line_id)
        if line is None:
            continue
        line.counted_quantity = None if counted is None else q3(counted)
        if line.counted_quantity is not None and line.counted_quantity < 0:
            raise BusinessRuleError("Counted quantities cannot be negative.")
        line.reason = (reason or "")[:300]
        line.save(update_fields=["counted_quantity", "reason"])
    return count


@transaction.atomic
def submit_count(count, user):
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.DRAFT:
        raise BusinessRuleError("Only a count in progress can be submitted.")
    lines = list(count.lines.select_related("product"))
    counted = [l for l in lines if l.counted_quantity is not None]
    if not counted:
        raise BusinessRuleError("Enter at least one counted quantity before submitting.")
    missing = [l.product.display_name for l in counted if l.variance != 0 and not l.reason.strip()]
    if missing:
        raise BusinessRuleError("Add a reason for each difference. Missing for: " + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""))
    count.status = StockCount.SUBMITTED
    count.submitted_by, count.submitted_at = user, timezone.now()
    count.save()
    log_action("stock.count_submit", business=count.business, user=user, obj=count)
    return count


@transaction.atomic
def approve_count(count, user, note=""):
    """Apply each variance as a ledger movement. Uses the variance (counted - snapshot), not an
    absolute overwrite, so sales made after the snapshot are preserved."""
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.SUBMITTED:
        raise BusinessRuleError("Only a submitted count can be approved.")
    applied = 0
    for line in count.lines.select_related("product").order_by("product_id"):
        variance = line.variance
        if variance is None or variance == 0:
            continue
        line.movement = record_movement(
            business=count.business, product=line.product, movement_type=StockMovement.RECONCILIATION,
            quantity=variance, user=user, unit_cost=line.unit_cost, reason=line.reason or "Stock count",
            source_type="stock_count", source_id=count.pk, source_ref=count.number, allow_negative=True,
        )
        line.save(update_fields=["movement"])
        applied += 1
    count.status = StockCount.APPROVED
    count.reviewed_by, count.reviewed_at, count.review_note = user, timezone.now(), note[:300]
    count.save()
    log_action("stock.count_approve", business=count.business, user=user, obj=count, after={"adjustments": applied})
    return count


@transaction.atomic
def reject_count(count, user, note=""):
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.SUBMITTED:
        raise BusinessRuleError("Only a submitted count can be rejected.")
    if not note.strip():
        raise BusinessRuleError("Please explain why the count is rejected.")
    count.status = StockCount.REJECTED
    count.reviewed_by, count.reviewed_at, count.review_note = user, timezone.now(), note[:300]
    count.save()
    log_action("stock.count_reject", business=count.business, user=user, obj=count, message=note)
    return count


@transaction.atomic
def cancel_count(count, user):
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status not in (StockCount.DRAFT, StockCount.SUBMITTED):
        raise BusinessRuleError("This count can no longer be cancelled.")
    count.status = StockCount.CANCELLED
    count.save()
    log_action("stock.count_cancel", business=count.business, user=user, obj=count)
    return count
