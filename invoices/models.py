from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BusinessScopedModel

from . import verification


class VerifiableMixin(models.Model):
    """Adds the QR verification token, human-typable short code and tamper-evident signature."""

    verify_token = models.CharField(max_length=32, unique=True, editable=False)
    short_code = models.CharField(max_length=14, unique=True, editable=False)
    signature = models.CharField(max_length=64, editable=False, blank=True)
    verify_count = models.PositiveIntegerField(default=0, editable=False)
    last_verified_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.verify_token:
            self.verify_token = verification.new_token()
            self.short_code = verification.new_short_code()
            self.signature = verification.compute_signature(self)
        super().save(*args, **kwargs)


class Invoice(VerifiableMixin, BusinessScopedModel):
    ISSUED, VOID = "issued", "void"
    number = models.CharField(max_length=30)
    sale = models.OneToOneField("sales.Sale", on_delete=models.PROTECT, related_name="invoice")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default=ISSUED, choices=[(ISSUED, "Issued"), (VOID, "Void")])
    terms = models.TextField(blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_invoice_number")]

    def __str__(self):
        return self.number


class Receipt(VerifiableMixin, BusinessScopedModel):
    number = models.CharField(max_length=30)
    customer = models.ForeignKey("contacts.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-issued_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["business", "number"], name="uniq_receipt_number")]

    def __str__(self):
        return self.number
