from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BusinessScopedModel


class PaymentMethod(BusinessScopedModel):
    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_payment_method")]

    def __str__(self):
        return self.name


class Payment(BusinessScopedModel):
    IN, OUT = "in", "out"
    DIRECTION_CHOICES = [(IN, "Money in"), (OUT, "Money out")]
    CUSTOMER_PAYMENT, SUPPLIER_PAYMENT, SALE_REFUND = "customer_payment", "supplier_payment", "sale_refund"
    PURPOSE_CHOICES = [(CUSTOMER_PAYMENT, "Customer payment"), (SUPPLIER_PAYMENT, "Supplier payment"),
                       (SALE_REFUND, "Sale refund")]
    POSTED, REVERSED = "posted", "reversed"

    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    customer = models.ForeignKey("contacts.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    supplier = models.ForeignKey("contacts.Supplier", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    sale = models.ForeignKey("sales.Sale", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    purchase = models.ForeignKey("purchases.PurchaseOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    receipt = models.ForeignKey("invoices.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_on = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, default=POSTED, choices=[(POSTED, "Posted"), (REVERSED, "Reversed")])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reverse_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(condition=~(Q(customer__isnull=False) & Q(supplier__isnull=False)), name="payment_single_party"),
        ]
        indexes = [
            models.Index(fields=["business", "-paid_on"]),
            models.Index(fields=["business", "direction", "status"]),
        ]

    def __str__(self):
        return f"{self.get_direction_display()} {self.amount}"


class PartyLedgerEntry(BusinessScopedModel):
    """Append-only customer/supplier account ledger. Balances are the SUM of entries.

    Customer: positive = customer owes us.  Supplier: positive = we owe the supplier.
    """

    SALE, PAYMENT, RETURN, REFUND, PURCHASE, PURCHASE_RETURN = "sale", "payment", "return", "refund", "purchase", "purchase_return"
    OPENING, ADJUSTMENT, REVERSAL = "opening", "adjustment", "reversal"
    TYPE_CHOICES = [(SALE, "Sale"), (PAYMENT, "Payment"), (RETURN, "Return"), (REFUND, "Refund"), (PURCHASE, "Purchase"),
                    (PURCHASE_RETURN, "Purchase return"), (OPENING, "Opening balance"), (ADJUSTMENT, "Adjustment"),
                    (REVERSAL, "Reversal")]

    customer = models.ForeignKey("contacts.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    supplier = models.ForeignKey("contacts.Supplier", null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    entry_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    entry_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=200)
    source_type = models.CharField(max_length=30, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["entry_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(customer__isnull=False) & Q(supplier__isnull=True)) | (Q(customer__isnull=True) & Q(supplier__isnull=False)),
                name="ledger_exactly_one_party"),
            models.CheckConstraint(condition=~Q(amount=0), name="ledger_amount_non_zero"),
        ]
        indexes = [
            models.Index(fields=["business", "customer", "entry_date"]),
            models.Index(fields=["business", "supplier", "entry_date"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Ledger entries are immutable. Post a reversing entry instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Ledger entries cannot be deleted.")
