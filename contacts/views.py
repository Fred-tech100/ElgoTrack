from django.contrib import messages
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.services import log_action
from core.mixins import BusinessFormMixin, BusinessQuerysetMixin, TenantRequiredMixin
from core.utils import date_range
from invoices import pdf
from payments import services as pay

from .forms import CustomerForm, SupplierForm
from .models import Customer, Supplier

from decimal import Decimal

ZERO = Decimal("0")


class PartyListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "contacts.view"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().annotate(bal=Coalesce(Sum("ledger_entries__amount"), ZERO))
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(name__icontains=g["q"]) | Q(phone__icontains=g["q"]) | Q(code__icontains=g["q"]) | Q(email__icontains=g["q"]))
        if g.get("owing"):
            qs = qs.filter(bal__gt=0)
        return qs.order_by("name")


class PartyFormMixin(TenantRequiredMixin, BusinessFormMixin):
    permission_required = "contacts.manage"
    template_name = "components/form_page.html"
    label = ""
    list_name = ""

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(title=f"{'Edit' if getattr(self, 'object', None) else 'New'} {self.label}",
                   cancel_url=reverse(self.list_name), submit_label="Save")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(f"{self.label}.save", obj=self.object, after={"name": self.object.name})
        messages.success(self.request, f"{self.object.name} saved.")
        return response

    def get_success_url(self):
        return reverse(self.detail_name, args=[self.object.pk])


class CustomerListView(PartyListView):
    model = Customer
    template_name = "contacts/customer_list.html"


class CustomerCreateView(PartyFormMixin, CreateView):
    model, form_class, label, list_name, detail_name = Customer, CustomerForm, "customer", "customers:list", "customers:detail"


class CustomerUpdateView(PartyFormMixin, BusinessQuerysetMixin, UpdateView):
    model, form_class, label, list_name, detail_name = Customer, CustomerForm, "customer", "customers:list", "customers:detail"


class SupplierListView(PartyListView):
    model = Supplier
    template_name = "contacts/supplier_list.html"


class SupplierCreateView(PartyFormMixin, CreateView):
    model, form_class, label, list_name, detail_name = Supplier, SupplierForm, "supplier", "suppliers:list", "suppliers:detail"


class SupplierUpdateView(PartyFormMixin, BusinessQuerysetMixin, UpdateView):
    model, form_class, label, list_name, detail_name = Supplier, SupplierForm, "supplier", "suppliers:list", "suppliers:detail"


class CustomerDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "contacts.view"
    model = Customer
    template_name = "contacts/customer_detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        start, end = date_range(self.request, 90)
        ctx.update(start=start, end=end, statement=pay.customer_statement(self.object, start, end),
                   open_sales=self.object.sales.filter(status="completed", balance_due__gt=0).order_by("sale_date"))
        return ctx


class SupplierDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "contacts.view"
    model = Supplier
    template_name = "contacts/supplier_detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        start, end = date_range(self.request, 90)
        ctx.update(start=start, end=end, statement=pay.supplier_statement(self.object, start, end),
                   purchases=self.object.purchases.exclude(status="cancelled")[:10])
        return ctx


class StatementPdfView(TenantRequiredMixin, View):
    permission_required = "finance.view"
    kind = "customer"

    def get(self, request, pk):
        model = Customer if self.kind == "customer" else Supplier
        party = get_object_or_404(model, pk=pk, business=request.business)
        start, end = date_range(request, 90)
        stmt = (pay.customer_statement if self.kind == "customer" else pay.supplier_statement)(party, start, end)
        data = pdf.render_statement(request.business, party, self.kind, start, end, stmt)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{self.kind}-statement-{party.code}.pdf"'
        return response


class CustomerStatementEmailView(TenantRequiredMixin, View):
    permission_required = "finance.view"

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk, business=request.business)
        if not customer.email:
            messages.error(request, "This customer does not have an email address on file.")
            return self._redirect(customer)
        start, end = date_range(request, 90)
        statement = pay.customer_statement(customer, start, end)
        attachment = pdf.render_statement(request.business, customer, "customer", start, end, statement)
        charges = sum((entry.amount for entry, _ in statement["rows"] if entry.amount > 0), ZERO)
        credits = -sum((entry.amount for entry, _ in statement["rows"] if entry.amount < 0), ZERO)
        business = request.business
        from emailing.services import queue_email

        delivery = queue_email(
            email_type="customer_statement",
            recipient=customer.email,
            subject=f"Customer statement from {business.name}",
            template="statement",
            context={
                "first_name": customer.name,
                "business_name": business.name,
                "period_start": start.strftime("%d %b %Y"),
                "period_end": end.strftime("%d %b %Y"),
                "opening_balance": f"{business.currency_code} {statement['opening']:,.{business.currency_decimals}f}",
                "charges": f"{business.currency_code} {charges:,.{business.currency_decimals}f}",
                "credits": f"{business.currency_code} {credits:,.{business.currency_decimals}f}",
                "closing_balance": f"{business.currency_code} {statement['closing']:,.{business.currency_decimals}f}",
                "cta_label": "Contact business" if business.email else "",
                "cta_url": f"mailto:{business.email}" if business.email else "",
            },
            business=business,
            user=request.user,
            related=customer,
            attachments=[(f"customer-statement-{customer.code}.pdf", attachment, "application/pdf")],
        )
        if delivery:
            messages.success(request, f"Statement email queued for {customer.email}.")
        else:
            messages.error(request, "The email could not be queued. Check the customer email and try again.")
        return self._redirect(customer)

    @staticmethod
    def _redirect(customer):
        return redirect("customers:detail", pk=customer.pk)
