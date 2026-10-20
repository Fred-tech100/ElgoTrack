from django.conf import settings
from django.db import models

from core.models import BusinessScopedModel


class Notification(BusinessScopedModel):
    """A business-wide alert, visible to users holding ``permission``. Read state is per user."""

    INFO, WARNING, DANGER = "info", "warning", "danger"
    SEVERITY_CHOICES = [(INFO, "Info"), (WARNING, "Warning"), (DANGER, "Urgent")]
    KIND_CHOICES = [("low_stock", "Low stock"), ("out_of_stock", "Out of stock"), ("credit_overdue", "Credit overdue"),
                    ("supplier_due", "Supplier payment due"), ("count_pending", "Stock count awaiting approval"),
                    ("expiry", "Expiry"), ("system", "System")]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=WARNING)
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=200, blank=True)
    permission = models.CharField(max_length=60, blank=True, help_text="Only users with this permission see it. Blank = everyone.")
    dedupe_key = models.CharField(max_length=80)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["business", "dedupe_key"], name="uniq_notification_key")]
        indexes = [models.Index(fields=["business", "resolved_at"])]

    def __str__(self):
        return self.title


class NotificationRead(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "user"], name="uniq_notification_read")]
