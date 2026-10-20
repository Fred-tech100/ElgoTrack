from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import BusinessScopedModel

ZERO = Decimal("0")


class Sale(BusinessScopedModel):
    COMPLETED, CANCELLED = "completed", "cancelled"
    STATUS_CHOICES = [(COMPLETED, "Completed"), (CANCELLED, "Cancelled")]
    PAID, PARTIAL, UNPAID, VOID = "paid", "partial", "unpaid", "void"
    PAYMENT_CHOICES = [(PAID, "Paid"), (PARTIAL, "Partially paid"), (UNPAID, "Unpaid"), (VOID, "Void")]

    number = models.CharField(max_length=30)
    client_token = models.CharField(max_length=64, blank=True, default="", help_text="Idempotency token from the till; prevents double-submitted sales.")
    customer = models.ForeignKey("contacts.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="sales")
    sale_date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=COMPLETED)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Cached from Payment / SaleReturn rows by refresh_payment_state()
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    returned_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default=UNPAID)
    due_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-sale_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "number"], name="uniq_sale_number"),
            models.UniqueConstraint(fields=["business", "client_token"], condition=~Q(client_token=""), name="uniq_sale_client_token"),
            models.CheckConstraint(condition=Q(total__gte=0), name="sale_total_non_negative"),
        ]
        indexes = [
            models.Index(fields=["business", "-sale_date"]),
            models.Index(fields=["business", "customer", "status"]),
            models.Index(fields=["business", "status", "balance_due"]),
        ]

    def __str__(self):
        return self.number

    @property
    def is_overdue(self):
        return bool(self.status == self.COMPLETED and self.balance_due > 0 and self.due_date
                    and self.due_date < timezone.localdate())

    def refresh_payment_state(self):
        """Recompute cached payment fields from the source Payment and Return rows."""
        from payments.models import Payment

        posted = self.payments.filter(status=Payment.POSTED)
        paid_in = posted.filter(direction=Payment.IN).aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]
        refunded = posted.filter(direction=Payment.OUT).aggregate(t=Coalesce(Sum("amount"), ZERO))["t"]
        self.returned_total = self.returns.aggregate(t=Coalesce(Sum("total"), ZERO))["t"]
        self.amount_paid = paid_in - refunded
        self.balance_due = max(self.total - self.returned_total - self.amount_paid, ZERO)
        if self.status == self.CANCELLED:
            self.payment_status = self.VOID
            self.balance_due = ZERO
        elif self.balance_due <= 0:
            self.payment_status = self.PAID
        elif self.amount_paid > 0:
            self.payment_status = self.PARTIAL
        else:
            self.payment_status = self.UNPAID
        self.save(update_fields=["amount_paid", "returned_total", "balance_due", "payment_status", "updated_at"])


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="sale_items")
    description = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Cost snapshot for profit reporting.")
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, help_text="Including tax.")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="saleitem_qty_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0, discount__gte=0), name="saleitem_amounts_non_negative"),
        ]
        indexes = [models.Index(fields=["product"])]

    @property
    def net_amount(self):
        return self.quantity * self.unit_price - self.discount

    def returned_quantity(self):
        return self.return_items.aggregate(t=Coalesce(Sum("quantity"), ZERO))["t"]


class SaleReturn(BusinessScopedModel):
    number = models.CharField(max_length=30)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=300)
    net_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-return_date"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_return_number")]

    def __str__(self):
        return self.number


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="returnitem_qty_positive")]
