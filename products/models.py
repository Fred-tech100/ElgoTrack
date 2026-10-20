from django.db import models

from core.models import BusinessScopedModel, next_sequence
from core.validators import validate_image_file


class Unit(BusinessScopedModel):
    name = models.CharField(max_length=40)
    abbreviation = models.CharField(max_length=10)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_unit_name")]
        ordering = ["name"]

    def __str__(self):
        return self.abbreviation or self.name


class Category(BusinessScopedModel):
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="uniq_category_name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(BusinessScopedModel):
    name = models.CharField(max_length=200)
    sku = models.CharField("SKU", max_length=60, blank=True)
    barcode = models.CharField(max_length=64, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    brand = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="products")
    purchase_price = models.DecimalField("Cost price", max_digits=14, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    wholesale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    min_stock_level = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0,
                                        help_text="Flag the product as low stock at or below this quantity.")
    supplier = models.ForeignKey("contacts.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    is_taxable = models.BooleanField(default=False)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                   help_text="Leave 0 to use the business default tax rate.")
    track_batches = models.BooleanField("Track batch and expiry", default=False)
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to="products/", blank=True, validators=[validate_image_file])
    variant_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="variants",
                                   help_text="Set when this product is a size/type variant of another product.")
    variant_label = models.CharField(max_length=60, blank=True, help_text="e.g. 50kg, 28G, 500mg")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "sku"], name="uniq_product_sku"),
            models.UniqueConstraint(fields=["business", "barcode"], condition=~models.Q(barcode=""), name="uniq_product_barcode"),
            models.CheckConstraint(condition=models.Q(purchase_price__gte=0, selling_price__gte=0), name="product_prices_non_negative"),
            models.CheckConstraint(condition=models.Q(min_stock_level__gte=0, reorder_level__gte=0), name="product_levels_non_negative"),
        ]
        indexes = [
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "is_active"]),
            models.Index(fields=["business", "barcode"]),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return f"{self.name} {self.variant_label}".strip()

    def save(self, *args, **kwargs):
        creating = self._state.adding
        if not self.sku:
            self.sku = f"SKU-{next_sequence(self.business, 'sku'):05d}"
        super().save(*args, **kwargs)
        if creating:
            from inventory.models import StockLevel

            StockLevel.objects.get_or_create(product=self, defaults={"business": self.business})

    def effective_tax_rate(self):
        if not self.business.tax_enabled or not self.is_taxable:
            return 0
        return self.tax_rate or self.business.default_tax_rate
