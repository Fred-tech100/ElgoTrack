from django.conf import settings
from django.db import models

from core.models import BusinessScopedModel


class SavedReport(BusinessScopedModel):
    """A named custom-report configuration (filters stored as plain parameters, dates relative)."""

    name = models.CharField(max_length=80)
    params = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_saved_report_name")]

    def __str__(self):
        return self.name
