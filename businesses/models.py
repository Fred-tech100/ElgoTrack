from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from core.models import TimeStampedModel
from core.validators import validate_image_file


class Business(TimeStampedModel):
    PENDING, ACTIVE, SUSPENDED = "pending", "active", "suspended"
    STATUS_CHOICES = [(PENDING, "Pending approval"), (ACTIVE, "Active"), (SUSPENDED, "Suspended")]
    TYPE_CHOICES = [
        ("hardware", "Hardware shop"), ("pharmacy", "Pharmacy / drug shop"), ("retail", "General retail"),
        ("supermarket", "Supermarket"), ("electronics", "Electronics shop"), ("agro", "Agro-input shop"),
        ("spareparts", "Spare parts"), ("wholesale", "Wholesale"), ("other", "Other"),
    ]

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True)
    business_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="retail")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ACTIVE, db_index=True)
    is_demo = models.BooleanField(default=False)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to="logos/", blank=True, validators=[validate_image_file])

    currency_code = models.CharField(max_length=5, default="UGX")
    currency_decimals = models.PositiveSmallIntegerField(default=0, help_text="Decimal places shown for money (UGX uses 0).")

    tax_enabled = models.BooleanField(default=False, help_text="Add tax to taxable products at the till. Prices are entered excluding tax.")
    tax_name = models.CharField(max_length=20, default="VAT")
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18)

    invoice_prefix = models.CharField(max_length=10, default="INV")
    receipt_prefix = models.CharField(max_length=10, default="RCT")
    invoice_terms = models.TextField(blank=True, help_text="Printed at the bottom of every invoice.")
    default_credit_days = models.PositiveSmallIntegerField(default=30)

    require_2fa = models.BooleanField("Require two-factor sign-in", default=False, help_text="Every user of this business must set up an authenticator app before using the system.")
    allow_negative_stock = models.BooleanField(default=False, help_text="Allow sales when the system shows insufficient stock.")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)

    class Meta:
        verbose_name_plural = "businesses"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=models.Q(currency_decimals__lte=4), name="currency_decimals_sane"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:150] or "business"
            slug, i = base, 1
            while Business.objects.filter(slug=slug).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)


class PlatformBillingSequence(models.Model):
    key = models.CharField(max_length=20)
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "year"], name="uniq_platform_billing_sequence")]


class PlatformVerifiableMixin(models.Model):
    verify_token = models.CharField(max_length=32, unique=True, editable=False)
    short_code = models.CharField(max_length=14, unique=True, editable=False)
    signature = models.CharField(max_length=64, editable=False, blank=True)
    verify_count = models.PositiveIntegerField(default=0, editable=False)
    last_verified_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.verify_token:
            from invoices import verification

            self.verify_token = verification.new_token()
            self.short_code = verification.new_short_code()
            self.signature = verification.compute_signature(self)
        super().save(*args, **kwargs)


class PlatformInvoice(PlatformVerifiableMixin, TimeStampedModel):
    ISSUED, VOID = "issued", "void"

    number = models.CharField(max_length=30, unique=True)
    business = models.ForeignKey(
        Business, null=True, blank=True, on_delete=models.SET_NULL, related_name="platform_invoices"
    )
    client_name = models.CharField(max_length=150)
    client_email = models.EmailField(blank=True)
    client_phone = models.CharField(max_length=30, blank=True)
    client_address = models.TextField(blank=True)
    currency_code = models.CharField(max_length=5, default="UGX")
    currency_decimals = models.PositiveSmallIntegerField(default=0)
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=[(ISSUED, "Issued"), (VOID, "Void")], default=ISSUED)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(subtotal__gte=0, tax_total__gte=0, total__gte=0, amount_paid__gte=0),
                                   name="platform_invoice_amounts_nonnegative"),
            models.CheckConstraint(condition=Q(currency_decimals__lte=4),
                                   name="platform_invoice_currency_decimals_sane"),
        ]

    def __str__(self):
        return self.number

    @property
    def balance_due(self):
        return max(self.total - self.amount_paid, 0)

    @property
    def payment_status(self):
        if self.status == self.VOID:
            return "void"
        if self.balance_due == 0:
            return "paid"
        return "partial" if self.amount_paid else "unpaid"


class PlatformInvoiceItem(models.Model):
    invoice = models.ForeignKey(PlatformInvoice, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="platform_invoice_item_qty_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="platform_invoice_item_price_nonnegative"),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name="platform_invoice_item_tax_range"),
        ]


class PlatformPayment(PlatformVerifiableMixin, TimeStampedModel):
    invoice = models.ForeignKey(PlatformInvoice, on_delete=models.PROTECT, related_name="payments")
    number = models.CharField(max_length=30, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    paid_on = models.DateField()
    method = models.CharField(max_length=40)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="platform_payment_amount_positive"),
            models.CheckConstraint(condition=Q(balance_after__gte=0), name="platform_payment_balance_nonnegative"),
        ]

    def __str__(self):
        return self.number

    @property
    def total_paid_after(self):
        return self.invoice.total - self.balance_after
