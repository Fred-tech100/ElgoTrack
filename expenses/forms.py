from django import forms
from django.utils import timezone

from core.forms import BusinessModelForm
from payments.models import PaymentMethod

from .models import Expense, ExpenseCategory


class ExpenseForm(BusinessModelForm):
    class Meta:
        model = Expense
        fields = ["expense_date", "category", "amount", "method", "description", "attachment"]
        widgets = {"expense_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = self.fields["category"].queryset.filter(is_active=True)
        self.fields["method"].queryset = self.fields["method"].queryset.filter(is_active=True)
        self.fields["expense_date"].initial = timezone.localdate

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return amount


class ExpenseCategoryForm(BusinessModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["name", "is_active"]
