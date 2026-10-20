from core.forms import BusinessModelForm

from .models import Customer, Supplier


class CustomerForm(BusinessModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "customer_type", "credit_limit", "notes", "is_active"]


class SupplierForm(BusinessModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "payment_terms_days", "notes", "is_active"]
