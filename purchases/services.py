from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.services import log_action
from contacts.models import Supplier
from core.exceptions import BusinessRuleError
from core.models import next_number
from core.utils import D, q2, q3
from inventory.models import StockMovement
from inventory.services import record_movement
from payments import services as pay
from payments.models import PartyLedgerEntry
from products.models import Product

from .models import (GoodsReceipt, GoodsReceiptItem, PurchaseItem, PurchaseOrder, PurchaseReturn,
                     PurchaseReturnItem)

ZERO = Decimal("0")


@transaction.atomic
def create_purchase(*, business, user, supplier, items, order_date=None, expected_date=None,
                    supplier_reference="", notes="", receive_now=False):
    """items: [{"product_id", "quantity", "unit_cost"}]."""
    if not items:
        raise BusinessRuleError("Add at least one product to the purchase.")
    supplier = Supplier.objects.get(pk=supplier.pk, business=business)
    ids = {int(i["product_id"]) for i in items}
    products = {p.pk: p for p in Product.objects.filter(business=business, pk__in=ids)}
    if len(products) != len(ids):
        raise BusinessRuleError("One or more products could not be found.")
    po = PurchaseOrder.objects.create(
        business=business, number=next_number(business, "po", "PO"), supplier=supplier,
        order_date=order_date or timezone.localdate(), expected_date=expected_date,
        supplier_reference=supplier_reference[:60], notes=notes[:300], created_by=user,
    )
    for raw in items:
        qty = q3(D(raw.get("quantity"), ZERO))
        cost = q2(D(raw.get("unit_cost"), ZERO))
        if qty <= 0:
            raise BusinessRuleError("Quantities must be greater than zero.")
        if cost < 0:
            raise BusinessRuleError("Unit cost cannot be negative.")
        PurchaseItem.objects.create(purchase=po, product=products[int(raw["product_id"])],
                                    quantity_ordered=qty, unit_cost=cost)
    po.refresh_totals()
    log_action("purchase.create", business=business, user=user, obj=po, after={"total": po.total})
    if receive_now:
        receive_goods(po, user, lines=[{"item_id": i.pk, "quantity": i.quantity_ordered} for i in po.items.all()],
                      supplier_reference=supplier_reference)
        po.refresh_from_db()
    return po


@transaction.atomic
def receive_goods(purchase, user, lines, received_on=None, supplier_reference="", notes=""):
    """lines: [{"item_id", "quantity", "batch_number", "expiry_date"}]. Partial deliveries are supported."""
    po = PurchaseOrder.objects.select_for_update().get(pk=purchase.pk, business=user.business)
    if po.status in (PurchaseOrder.CANCELLED, PurchaseOrder.RECEIVED):
        raise BusinessRuleError(f"Goods cannot be received on a {po.get_status_display().lower()} purchase.")
    items = {i.pk: i for i in po.items.select_related("product")}
    chosen = []
    for ln in lines:
        qty = q3(D(ln.get("quantity"), ZERO))
        if qty <= 0:
            continue
        item = items.get(int(ln["item_id"]))
        if item is None:
            raise BusinessRuleError("Unknown purchase item.")
        if qty > item.outstanding:
            raise BusinessRuleError(f"Only {item.outstanding.normalize():f} of {item.product.display_name} is still outstanding.")
        chosen.append((item, qty, ln))
    if not chosen:
        raise BusinessRuleError("Enter a received quantity for at least one item.")
    grn = GoodsReceipt.objects.create(
        business=po.business, purchase=po, number=next_number(po.business, "grn", "GRN"),
        received_on=received_on or timezone.localdate(), supplier_reference=supplier_reference[:60],
        notes=notes[:300], received_by=user,
    )
    value = ZERO
    for item, qty, ln in sorted(chosen, key=lambda t: t[0].product_id):
        movement = record_movement(
            business=po.business, product=item.product, movement_type=StockMovement.PURCHASE, quantity=qty,
            user=user, unit_cost=item.unit_cost, source_type="purchase", source_id=po.pk, source_ref=po.number,
            batch_number=ln.get("batch_number", "") or "", expiry_date=ln.get("expiry_date") or None,
        )
        # Moving-average cost: blend existing positive stock with the new delivery.
        old_qty = max(movement.balance_after - qty, ZERO)
        new_cost = (old_qty * item.product.purchase_price + qty * item.unit_cost) / (old_qty + qty)
        item.product.purchase_price = q2(new_cost)
        item.product.save(update_fields=["purchase_price", "updated_at"])
        item.quantity_received += qty
        item.save(update_fields=["quantity_received"])
        GoodsReceiptItem.objects.create(receipt=grn, purchase_item=item, quantity=qty, unit_cost=item.unit_cost,
                                        batch_number=ln.get("batch_number", "") or "",
                                        expiry_date=ln.get("expiry_date") or None)
        value += qty * item.unit_cost
    value = q2(value)
    po.status = PurchaseOrder.RECEIVED if all(i.outstanding == 0 for i in po.items.all()) else PurchaseOrder.PARTIAL
    po.save(update_fields=["status", "updated_at"])
    pay.post_ledger(business=po.business, user=user, amount=value, entry_type=PartyLedgerEntry.PURCHASE,
                    supplier=po.supplier, description=f"Goods received {grn.number} ({po.number})",
                    entry_date=grn.received_on, source_type="goods_receipt", source_id=grn.pk)
    po.refresh_totals()
    log_action("purchase.receive", business=po.business, user=user, obj=po, after={"grn": grn.number, "value": value})
    return grn


@transaction.atomic
def cancel_purchase(purchase, user, reason=""):
    po = PurchaseOrder.objects.select_for_update().get(pk=purchase.pk, business=user.business)
    if po.receipts.exists():
        raise BusinessRuleError("A purchase with received goods cannot be cancelled. Record a return instead.")
    if po.status == PurchaseOrder.CANCELLED:
        raise BusinessRuleError("This purchase is already cancelled.")
    po.status = PurchaseOrder.CANCELLED
    po.save(update_fields=["status", "updated_at"])
    log_action("purchase.cancel", business=po.business, user=user, obj=po, message=reason)
    return po


@transaction.atomic
def return_goods(purchase, user, lines, reason):
    """lines: [{"item_id", "quantity"}] — sends stock back to the supplier and reduces what we owe."""
    po = PurchaseOrder.objects.select_for_update().get(pk=purchase.pk, business=user.business)
    if not reason.strip():
        raise BusinessRuleError("Please give a reason for the return.")
    items = {i.pk: i for i in po.items.select_related("product")}
    chosen = []
    for ln in lines:
        qty = q3(D(ln.get("quantity"), ZERO))
        if qty <= 0:
            continue
        item = items.get(int(ln["item_id"]))
        if item is None:
            raise BusinessRuleError("Unknown purchase item.")
        if qty > item.quantity_received - item.quantity_returned:
            raise BusinessRuleError(f"Cannot return more than was received for {item.product.display_name}.")
        chosen.append((item, qty))
    if not chosen:
        raise BusinessRuleError("Enter a quantity for at least one item to return.")
    ret = PurchaseReturn.objects.create(business=po.business, number=next_number(po.business, "prt", "PRT"),
                                        purchase=po, reason=reason[:300], created_by=user)
    total = ZERO
    for item, qty in sorted(chosen, key=lambda t: t[0].product_id):
        record_movement(
            business=po.business, product=item.product, movement_type=StockMovement.PURCHASE_RETURN, quantity=-qty,
            user=user, unit_cost=item.unit_cost, reason=reason, source_type="purchase_return", source_id=ret.pk,
            source_ref=ret.number,
        )
        item.quantity_returned += qty
        item.save(update_fields=["quantity_returned"])
        PurchaseReturnItem.objects.create(purchase_return=ret, purchase_item=item, quantity=qty, unit_cost=item.unit_cost)
        total += qty * item.unit_cost
    ret.total = q2(total)
    ret.save(update_fields=["total", "updated_at"])
    pay.post_ledger(business=po.business, user=user, amount=-ret.total, entry_type=PartyLedgerEntry.PURCHASE_RETURN,
                    supplier=po.supplier, description=f"Return {ret.number} ({po.number})",
                    source_type="purchase_return", source_id=ret.pk)
    po.refresh_totals()
    log_action("purchase.return", business=po.business, user=user, obj=po, after={"return": ret.number, "total": ret.total})
    return ret
