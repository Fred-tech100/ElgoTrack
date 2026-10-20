from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class EmailDelivery(TimeStampedModel):
    PENDING, SENDING, SENT, FAILED = "pending", "sending", "sent", "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (SENDING, "Sending"), (SENT, "Sent"), (FAILED, "Failed")]

    email_type = models.CharField(max_length=40, db_index=True)
    recipient = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    provider_message_id = models.CharField(max_length=150, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    related_type = models.CharField(max_length=60, blank=True)
    related_id = models.CharField(max_length=80, blank=True)
    business = models.ForeignKey("businesses.Business", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email_type} to {self.recipient} ({self.status})"
