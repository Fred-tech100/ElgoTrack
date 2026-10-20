from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.models import BusinessScopedModel


class StockLevel(BusinessScopedModel):
    """Current on-hand quantity. Changed ONLY by ``inventory.services.record_movement``."""

    product = models.OneToOneField("products.Product", on_delete=models.CASCADE, related_name="stock")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.product_id}: {self.quantity}"


class StockMovement(BusinessScopedModel):
    OPENING, PURCHASE, SALE = "opening", "purchase", "sale"
    SALE_RETURN, PURCHASE_RETURN, SALE_CANCEL = "sale_return", "purchase_return", "sale_cancel"
    ADJUSTMENT, DAMAGE, LOSS = "adjustment", "damage", "loss"
    RECONCILIATION, TRANSFER = "reconciliation", "transfer"
    TYPE_CHOICES = [
        (OPENING, "Opening balance"), (PURCHASE, "Purchase"), (SALE, "Sale"), (SALE_RETURN, "Sale return"),
        (PURCHASE_RETURN, "Purchase return"), (SALE_CANCEL, "Sale cancelled"), (ADJUSTMENT, "Adjustment"),
        (DAMAGE, "Damage"), (LOSS, "Loss"), (RECONCILIATION, "Stock count"), (TRANSFER, "Transfer"),
    ]

    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Signed: positive adds stock, negative removes.")
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    source_type = models.CharField(max_length=30, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_ref = models.CharField(max_length=40, blank=True)
    batch_number = models.CharField(max_length=60, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=~Q(quantity=0), name="movement_quantity_non_zero")]
        indexes = [
            models.Index(fields=["business", "product", "-created_at"]),
            models.Index(fields=["business", "movement_type", "-created_at"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Stock movements are immutable. Record a new movement to correct stock.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Stock movements cannot be deleted.")

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity} of {self.product_id}"


class StockCount(BusinessScopedModel):
    DRAFT, SUBMITTED, APPROVED, REJECTED, CANCELLED = "draft", "submitted", "approved", "rejected", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Counting"), (SUBMITTED, "Awaiting approval"), (APPROVED, "Approved"),
                      (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    number = models.CharField(max_length=30)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    category = models.ForeignKey("products.Category", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=300, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_count_number")]

    def __str__(self):
        return self.number


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    counted_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    movement = models.ForeignKey(StockMovement, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["count", "product"], name="uniq_count_product")]
        ordering = ["product__name"]

    @property
    def variance(self):
        return None if self.counted_quantity is None else self.counted_quantity - self.expected_quantity

    @property
    def variance_value(self):
        v = self.variance
        return None if v is None else v * self.unit_cost
