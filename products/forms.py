from django import forms

from core.forms import BusinessModelForm

from .models import Category, Product, Unit


class ProductForm(BusinessModelForm):
    opening_quantity = forms.DecimalField(
        required=False, min_value=0, max_digits=14, decimal_places=3,
        help_text="Optional. Records an opening-balance stock movement.",
    )

    class Meta:
        model = Product
        fields = ["name", "variant_of", "variant_label", "sku", "barcode", "category", "brand", "unit",
                  "purchase_price", "selling_price", "wholesale_price", "min_stock_level", "reorder_level",
                  "supplier", "is_taxable", "tax_rate", "track_batches", "image", "description", "is_active"]

    def __init__(self, *args, can_set_opening=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sku"].help_text = "Leave blank to generate automatically."
        if self.instance.pk or not can_set_opening:
            del self.fields["opening_quantity"]
        if self.instance.pk:
            self.fields["variant_of"].queryset = self.fields["variant_of"].queryset.exclude(pk=self.instance.pk)

    def clean(self):
        data = super().clean()
        parent = data.get("variant_of")
        if parent and parent.variant_of_id:
            self.add_error("variant_of", "Choose a main product, not another variant.")
        return data


class CategoryForm(BusinessModelForm):
    class Meta:
        model = Category
        fields = ["name", "description"]


class UnitForm(BusinessModelForm):
    class Meta:
        model = Unit
        fields = ["name", "abbreviation"]
