from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce

from core.models import BusinessScopedModel, next_sequence


class _Party(BusinessScopedModel):
    PREFIX = "P"
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=250, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"{self.PREFIX}-{next_sequence(self.business, self.PREFIX.lower()):05d}"
        super().save(*args, **kwargs)

    def _balance(self, field):
        from payments.models import PartyLedgerEntry

        return PartyLedgerEntry.objects.filter(**{field: self}).aggregate(t=Coalesce(Sum("amount"), Decimal("0")))["t"]


class Customer(_Party):
    PREFIX = "CUS"
    CASH, CREDIT = "cash", "credit"
    TYPE_CHOICES = [(CASH, "Cash customer"), (CREDIT, "Credit customer")]
    customer_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=CASH)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                       help_text="Maximum amount this customer may owe. 0 means no limit.")

    class Meta(_Party.Meta):
        constraints = [
            models.UniqueConstraint(fields=["business", "code"], name="uniq_customer_code"),
            models.CheckConstraint(condition=models.Q(credit_limit__gte=0), name="customer_credit_limit_non_negative"),
        ]
        indexes = [models.Index(fields=["business", "name"]), models.Index(fields=["business", "phone"])]

    @property
    def balance(self):
        """Amount the customer owes us (negative = we owe them). Derived from the ledger."""
        return self._balance("customer")


class Supplier(_Party):
    PREFIX = "SUP"
    contact_person = models.CharField(max_length=120, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=0)

    class Meta(_Party.Meta):
        constraints = [models.UniqueConstraint(fields=["business", "code"], name="uniq_supplier_code")]
        indexes = [models.Index(fields=["business", "name"])]

    @property
    def balance(self):
        """Amount we owe the supplier. Derived from the ledger."""
        return self._balance("supplier")
