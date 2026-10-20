from django import forms
from django.contrib.auth.password_validation import validate_password
from decimal import Decimal

from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from accounts.models import User
from core.forms import StyledForm, StyledFormMixin

from .models import Business, PlatformInvoice


class BusinessSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name", "business_type", "email", "phone", "address", "logo", "currency_code", "currency_decimals",
                  "tax_enabled", "tax_name", "default_tax_rate", "invoice_prefix", "receipt_prefix",
                  "invoice_terms", "default_credit_days", "allow_negative_stock", "fiscal_year_start_month", "require_2fa"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_fiscal_year_start_month(self):
        month = self.cleaned_data["fiscal_year_start_month"]
        if not 1 <= month <= 12:
            raise forms.ValidationError("Enter a month between 1 and 12.")
        return month


class NewBusinessForm(StyledForm):
    name = forms.CharField(label="Business name", max_length=150)
    business_type = forms.ChoiceField(choices=Business.TYPE_CHOICES)
    owner_name = forms.CharField(label="Owner's full name", max_length=120)
    owner_email = forms.EmailField(label="Owner's email")
    owner_phone = forms.CharField(label="Owner's phone number", max_length=30)

    def clean_owner_email(self):
        email = self.cleaned_data["owner_email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class RegisterForm(NewBusinessForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Password",
                               help_text="At least 10 characters. Avoid common words and your own name or email.")
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Confirm password")

    def clean(self):
        data = super().clean()
        pw, confirm = data.get("password"), data.get("password_confirm")
        if pw:
            from django.contrib.auth.password_validation import validate_password as _validate
            from accounts.models import User
            try:
                _validate(pw, User(email=data.get("owner_email", ""), full_name=data.get("owner_name", "")))
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        if pw and confirm and pw != confirm:
            self.add_error("password_confirm", "The two passwords do not match.")
        return data


class PlatformInvoiceForm(StyledForm):
    client_business = forms.ModelChoiceField(
        queryset=Business.objects.order_by("name"), required=False, label="ElgoTrack business",
        empty_label="External client (enter their details below)",
    )
    client_name = forms.CharField(max_length=150, required=False, label="Client name")
    client_email = forms.EmailField(required=False, label="Client email")
    client_phone = forms.CharField(max_length=30, required=False, label="Client phone")
    client_address = forms.CharField(required=False, widget=forms.Textarea, label="Client address")
    currency_code = forms.RegexField(regex=r"^[A-Za-z]{3,5}$", max_length=5, initial="UGX", label="Currency code")
    currency_decimals = forms.TypedChoiceField(
        choices=[(i, str(i)) for i in range(5)], coerce=int, initial=0, label="Currency decimal places",
    )
    issue_date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea)
    terms = forms.CharField(required=False, widget=forms.Textarea,
                            help_text="Payment terms or service conditions printed on this invoice.")

    def clean(self):
        cleaned = super().clean()
        business = cleaned.get("client_business")
        if not business and not cleaned.get("client_name"):
            self.add_error("client_name", "Enter a client name or select an ElgoTrack business.")
        if business and not cleaned.get("client_name"):
            cleaned["client_name"] = business.name
        if cleaned.get("due_date") and cleaned.get("issue_date") and cleaned["due_date"] < cleaned["issue_date"]:
            self.add_error("due_date", "The due date cannot be before the issue date.")
        return cleaned


class PlatformInvoiceItemForm(StyledForm):
    description = forms.CharField(max_length=250, label="Service or item")
    quantity = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0.001)
    unit_price = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, label="Unit price")
    tax_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False,
                                  label="Tax rate (%)")

    def clean_tax_rate(self):
        return self.cleaned_data.get("tax_rate") or 0


class PlatformInvoiceItemFormSetBase(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        total = Decimal("0")
        for form in self.forms:
            item = form.cleaned_data
            if item:
                net = (item["quantity"] * item["unit_price"]).quantize(Decimal("0.01"))
                tax = (net * item["tax_rate"] / Decimal("100")).quantize(Decimal("0.01"))
                total += net + tax
        if total > Decimal("999999999999.99"):
            raise forms.ValidationError("The invoice total exceeds the supported amount.")


PlatformInvoiceItemFormSet = formset_factory(
    PlatformInvoiceItemForm, formset=PlatformInvoiceItemFormSetBase, extra=2, min_num=1, validate_min=True,
    max_num=25, validate_max=True,
)


class PlatformPaymentForm(StyledForm):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    paid_on = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    method = forms.CharField(max_length=40, initial="Bank", help_text="For example, bank transfer, mobile money, cash, or card.")
    reference = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(max_length=300, required=False)
