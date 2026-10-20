from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, FormView, ListView, UpdateView

from core.exceptions import BusinessRuleError
from core.mixins import BusinessFormMixin, BusinessQuerysetMixin, TenantRequiredMixin
from core.utils import parse_date

from . import services
from .forms import CustomerPaymentForm, PaymentMethodForm, SupplierPaymentForm
from .models import Payment, PaymentMethod


class PaymentListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = ("payments.view", "payments.receive")
    model = Payment
    template_name = "payments/list.html"
    paginate_by = 30

    def get_queryset(self):
        qs = super().get_queryset().select_related("customer", "supplier", "method", "sale", "receipt")
        g = self.request.GET
        if g.get("direction"):
            qs = qs.filter(direction=g["direction"])
        if g.get("method"):
            qs = qs.filter(method_id=g["method"])
        if g.get("q"):
            qs = qs.filter(Q(customer__name__icontains=g["q"]) | Q(supplier__name__icontains=g["q"]) | Q(reference__icontains=g["q"]) | Q(receipt__number__icontains=g["q"]))
        if parse_date(g.get("start")):
            qs = qs.filter(paid_on__gte=parse_date(g["start"]))
        if parse_date(g.get("end")):
            qs = qs.filter(paid_on__lte=parse_date(g["end"]))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["methods"] = PaymentMethod.objects.filter(business=self.request.business)
        return ctx


class CustomerPaymentView(TenantRequiredMixin, BusinessFormMixin, FormView):
    permission_required = "payments.receive"
    form_class = CustomerPaymentForm
    template_name = "components/form_page.html"
    extra_context = {"title": "Receive customer payment", "cancel_url": reverse_lazy("payments:list"), "submit_label": "Record payment",
                     "intro": "The payment is applied to the customer's oldest unpaid sales first."}

    def get_initial(self):
        return {"customer": self.request.GET.get("customer")}

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            receipt, _ = services.receive_customer_payment(
                business=self.request.business, user=self.request.user, customer=d["customer"], amount=d["amount"],
                method=d["method"], paid_on=d["paid_on"], reference=d["reference"], notes=d["notes"])
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Payment recorded. Receipt {receipt.number}.")
        return redirect("invoices:receipt_detail", pk=receipt.pk) if self.request.user.has_business_perm("invoices.view") else redirect("payments:list")


class SupplierPaymentView(TenantRequiredMixin, BusinessFormMixin, FormView):
    permission_required = "payments.pay_supplier"
    form_class = SupplierPaymentForm
    template_name = "components/form_page.html"
    extra_context = {"title": "Pay supplier", "cancel_url": reverse_lazy("payments:list"), "submit_label": "Record payment"}

    def get_initial(self):
        return {"supplier": self.request.GET.get("supplier"), "purchase": self.request.GET.get("purchase")}

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            services.pay_supplier(business=self.request.business, user=self.request.user, supplier=d["supplier"],
                                  amount=d["amount"], method=d["method"], purchase=d["purchase"], paid_on=d["paid_on"],
                                  reference=d["reference"], notes=d["notes"])
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Supplier payment recorded.")
        return redirect("suppliers:detail", pk=d["supplier"].pk)


class PaymentReverseView(TenantRequiredMixin, View):
    permission_required = "payments.reverse"

    def post(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk, business=request.business)
        try:
            services.reverse_payment(payment, request.user, request.POST.get("reason", ""))
            messages.success(request, "Payment reversed.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            target = reverse("payments:list")
        return redirect(target)


class MethodListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "settings.manage"
    model = PaymentMethod
    template_name = "payments/methods.html"


class MethodCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "settings.manage"
    model = PaymentMethod
    form_class = PaymentMethodForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("payments:methods")
    extra_context = {"title": "New payment method", "cancel_url": reverse_lazy("payments:methods"), "submit_label": "Save"}


class MethodUpdateView(TenantRequiredMixin, BusinessQuerysetMixin, BusinessFormMixin, UpdateView):
    permission_required = "settings.manage"
    model = PaymentMethod
    form_class = PaymentMethodForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("payments:methods")
    extra_context = {"title": "Edit payment method", "cancel_url": reverse_lazy("payments:methods"), "submit_label": "Save"}
