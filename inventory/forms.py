from django import forms

from core.forms import BusinessForm
from products.models import Category, Product

from .models import StockMovement
from .services import MANUAL_TYPES


class AdjustmentForm(BusinessForm):
    product = forms.ModelChoiceField(Product.objects.filter(is_active=True))
    movement_type = forms.ChoiceField(choices=[(t, l) for t, l in StockMovement.TYPE_CHOICES if t in MANUAL_TYPES], label="Type")
    quantity = forms.DecimalField(max_digits=14, decimal_places=3,
                                  help_text="For Damage and Loss enter a positive number. For Adjustment use + or -.")
    reason = forms.CharField(max_length=300, required=False)
    batch_number = forms.CharField(max_length=60, required=False)
    expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


class CountStartForm(BusinessForm):
    category = forms.ModelChoiceField(Category.objects.all(), required=False, empty_label="All products")
    notes = forms.CharField(max_length=300, required=False)
