from django import forms
from django.utils import timezone

from contacts.models import Customer, Supplier
from core.forms import BusinessForm, BusinessModelForm
from purchases.models import PurchaseOrder

from .models import PaymentMethod


class _PaymentBase(BusinessForm):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    method = forms.ModelChoiceField(PaymentMethod.objects.filter(is_active=True))
    paid_on = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(max_length=80, required=False, help_text="Mobile money or bank reference")
    notes = forms.CharField(max_length=300, required=False)


class CustomerPaymentForm(_PaymentBase):
    field_order = ["customer", "amount", "method", "paid_on", "reference", "notes"]
    customer = forms.ModelChoiceField(Customer.objects.filter(is_active=True))


class SupplierPaymentForm(_PaymentBase):
    field_order = ["supplier", "purchase", "amount", "method", "paid_on", "reference", "notes"]
    supplier = forms.ModelChoiceField(Supplier.objects.filter(is_active=True))
    purchase = forms.ModelChoiceField(PurchaseOrder.objects.exclude(status="cancelled"), required=False,
                                      help_text="Optional: link the payment to a purchase.")

    def clean(self):
        data = super().clean()
        if data.get("purchase") and data.get("supplier") and data["purchase"].supplier_id != data["supplier"].pk:
            self.add_error("purchase", "This purchase belongs to a different supplier.")
        return data


class PaymentMethodForm(BusinessModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "sort_order", "is_active"]
