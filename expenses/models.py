from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BusinessScopedModel
from core.validators import validate_attachment


class ExpenseCategory(BusinessScopedModel):
    name = models.CharField(max_length=60)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "expense categories"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_expense_category")]

    def __str__(self):
        return self.name


class Expense(BusinessScopedModel):
    POSTED, VOID = "posted", "void"
    number = models.CharField(max_length=30)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    expense_date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=250)
    method = models.ForeignKey("payments.PaymentMethod", on_delete=models.PROTECT, related_name="expenses")
    attachment = models.FileField(upload_to="expenses/", blank=True, validators=[validate_attachment])
    status = models.CharField(max_length=10, default=POSTED, choices=[(POSTED, "Posted"), (VOID, "Void")])
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "number"], name="uniq_expense_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
        ]
        indexes = [models.Index(fields=["business", "-expense_date"]), models.Index(fields=["business", "category"])]

    def __str__(self):
        return self.number
