from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import BusinessScopedModel

ZERO = Decimal("0")


class PurchaseOrder(BusinessScopedModel):
    DRAFT, ORDERED, PARTIAL, RECEIVED, CANCELLED = "draft", "ordered", "partially_received", "received", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (ORDERED, "Ordered"), (PARTIAL, "Partially received"),
                      (RECEIVED, "Received"), (CANCELLED, "Cancelled")]

    number = models.CharField(max_length=30)
    supplier = models.ForeignKey("contacts.Supplier", on_delete=models.PROTECT, related_name="purchases")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ORDERED)
    order_date = models.DateField(default=timezone.localdate)
    expected_date = models.DateField(null=True, blank=True)
    supplier_reference = models.CharField(max_length=60, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    total = models.DecimalField("Ordered value", max_digits=14, decimal_places=2, default=0)
    received_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    returned_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-order_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_po_number")]
        indexes = [models.Index(fields=["business", "-order_date"]), models.Index(fields=["business", "supplier"])]

    def __str__(self):
        return self.number

    @property
    def balance_due(self):
        """What we still owe for goods actually received (net of returns)."""
        return max(self.received_value - self.returned_value - self.amount_paid, ZERO)

    def refresh_totals(self):
        from payments.models import Payment

        self.total = sum((i.quantity_ordered * i.unit_cost for i in self.items.all()), ZERO)
        self.received_value = sum((i.quantity_received * i.unit_cost for i in self.items.all()), ZERO)
        self.returned_value = self.returns.aggregate(t=Coalesce(Sum("total"), ZERO))["t"]
        self.amount_paid = self.payments.filter(status=Payment.POSTED, direction=Payment.OUT).aggregate(
            t=Coalesce(Sum("amount"), ZERO))["t"]
        self.save(update_fields=["total", "received_value", "returned_value", "amount_paid", "updated_at"])


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="purchase_items")
    quantity_ordered = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_returned = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity_ordered__gt=0), name="poitem_qty_positive"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="poitem_cost_non_negative"),
        ]

    @property
    def line_total(self):
        return self.quantity_ordered * self.unit_cost

    @property
    def outstanding(self):
        return max(self.quantity_ordered - self.quantity_received, ZERO)


class GoodsReceipt(BusinessScopedModel):
    number = models.CharField(max_length=30)
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    received_on = models.DateField(default=timezone.localdate)
    supplier_reference = models.CharField(max_length=60, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-received_on", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_grn_number")]


class GoodsReceiptItem(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="items")
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT, related_name="receipt_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    batch_number = models.CharField(max_length=60, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="grnitem_qty_positive")]


class PurchaseReturn(BusinessScopedModel):
    number = models.CharField(max_length=30)
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=300)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-return_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_prt_number")]


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="items")
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="prtitem_qty_positive")]
