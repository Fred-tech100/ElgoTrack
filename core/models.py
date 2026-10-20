from django.db import models, transaction


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BusinessScopedModel(TimeStampedModel):
    """Base for every tenant-owned record. Always filter by ``business``."""

    business = models.ForeignKey("businesses.Business", on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True


class DocumentSequence(models.Model):
    """Gapless-per-key counters used for invoice, receipt, PO numbers etc."""

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="sequences")
    key = models.CharField(max_length=30)
    year = models.PositiveSmallIntegerField(default=0)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "key", "year"], name="uniq_sequence")]

    def __str__(self):
        return f"{self.business_id}:{self.key}:{self.year}={self.last_number}"


def next_sequence(business, key, year=0):
    """Atomically allocate the next number for (business, key, year)."""
    with transaction.atomic():
        seq, _ = DocumentSequence.objects.select_for_update().get_or_create(
            business=business, key=key, year=year
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number"])
        return seq.last_number


def next_number(business, key, prefix, *, yearly=True, width=5):
    from django.utils import timezone

    year = timezone.localdate().year if yearly else 0
    n = next_sequence(business, key, year)
    return f"{prefix}-{year}-{n:0{width}d}" if yearly else f"{prefix}-{n:0{width}d}"
