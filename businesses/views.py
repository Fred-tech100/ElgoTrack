from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from accounts.models import User
from accounts.services import send_invitation
from audit.models import AuditLog
from audit.services import log_action
from core.mixins import PlatformAdminRequiredMixin, TenantRequiredMixin
from core.exceptions import BusinessRuleError

from . import services
from .forms import (
    BusinessSettingsForm, NewBusinessForm, PlatformInvoiceForm, PlatformInvoiceItemFormSet,
    PlatformPaymentForm, RegisterForm,
)
from .models import Business, PlatformInvoice, PlatformPayment


# ---------------------------------------------------------------- business settings
class BusinessSettingsView(TenantRequiredMixin, UpdateView):
    permission_required = "settings.manage"
    form_class = BusinessSettingsForm
    template_name = "businesses/settings.html"
    success_url = reverse_lazy("businesses:settings")

    def get_object(self, queryset=None):
        return self.request.business

    def form_valid(self, form):
        before = {f: str(form.initial.get(f)) for f in form.changed_data if f != "logo"}
        response = super().form_valid(form)
        log_action("business.settings", obj=self.object, before=before,
                   after={f: str(form.cleaned_data[f]) for f in before})
        messages.success(self.request, "Business settings saved.")
        return response


# ---------------------------------------------------------------- self-service registration
class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        d = form.cleaned_data
        business, owner = services.create_business(
            name=d["name"], business_type=d["business_type"], owner_email=d["owner_email"],
            owner_name=d["owner_name"], owner_phone=d["owner_phone"], owner_password=d["password"],
            status=Business.PENDING,
        )
        from emailing.services import notify_platform_admins_of_registration, send_welcome

        send_welcome(owner, awaiting_approval=True)
        review_url = self.request.build_absolute_uri(
            reverse("platform:business_detail", args=[business.pk])
        )
        notify_platform_admins_of_registration(
            business=business,
            owner=owner,
            review_url=review_url,
        )
        messages.success(self.request, "Thanks! Your business is awaiting approval. You can sign in once it is approved.")
        return super().form_valid(form)


# ---------------------------------------------------------------- platform (super admin)
class PlatformDashboardView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "platform/dashboard.html"

    def get_context_data(self, **kw):
        from products.models import Product
        from sales.models import Sale

        ctx = super().get_context_data(**kw)
        counts = Business.objects.aggregate(
            total=Count("id"), active=Count("id", filter=Q(status="active")),
            suspended=Count("id", filter=Q(status="suspended")), pending=Count("id", filter=Q(status="pending")),
        )
        ctx.update(
            counts=counts,
            users=User.objects.filter(business__isnull=False).count(),
            products=Product.objects.count(),
            sales=Sale.objects.count(),
            pending=Business.objects.filter(status="pending"),
            failed_logins=AuditLog.objects.filter(action="auth.login_failed").count(),
            recent_logs=AuditLog.objects.select_related("business")[:10],
        )
        return ctx


class PlatformBusinessListView(PlatformAdminRequiredMixin, ListView):
    template_name = "platform/business_list.html"
    paginate_by = 25

    def get_queryset(self):
        qs = Business.objects.annotate(user_count=Count("users", distinct=True)).order_by("-created_at")
        q, status = self.request.GET.get("q"), self.request.GET.get("status")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
        if status:
            qs = qs.filter(status=status)
        return qs


class PlatformBusinessCreateView(PlatformAdminRequiredMixin, FormView):
    template_name = "components/form_page.html"
    form_class = NewBusinessForm
    extra_context = {"title": "Create business", "cancel_url": reverse_lazy("platform:businesses"),
                     "intro": "The owner receives an email invitation to set their password.", "submit_label": "Create business"}

    def form_valid(self, form):
        d = form.cleaned_data
        business, owner = services.create_business(
            name=d["name"], business_type=d["business_type"], owner_email=d["owner_email"], owner_name=d["owner_name"],
            owner_phone=d["owner_phone"],
        )
        send_invitation(self.request, owner, inviter=self.request.user)
        messages.success(self.request, f"{business.name} created.")
        return redirect("platform:business_detail", pk=business.pk)


class PlatformBusinessDetailView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "platform/business_detail.html"

    def get_context_data(self, pk, **kw):
        from products.models import Product
        from sales.models import Sale

        ctx = super().get_context_data(**kw)
        business = get_object_or_404(Business, pk=pk)
        ctx.update(
            obj=business,
            users=business.users.select_related("role"),
            product_count=Product.objects.filter(business=business).count(),
            sale_count=Sale.objects.filter(business=business).count(),
            logs=AuditLog.objects.filter(business=business)[:15],
        )
        return ctx


class PlatformBusinessActionView(PlatformAdminRequiredMixin, View):
    ACTIONS = {"approve": "active", "reactivate": "active", "suspend": "suspended"}

    def post(self, request, pk, action):
        business = get_object_or_404(Business, pk=pk)
        if action not in self.ACTIONS:
            messages.error(request, "Unknown action.")
        else:
            services.set_status(business, self.ACTIONS[action], user=request.user)
            messages.success(request, f"{business.name}: {action} done.")
        return redirect("platform:business_detail", pk=pk)


class PlatformUserListView(PlatformAdminRequiredMixin, ListView):
    template_name = "platform/user_list.html"
    paginate_by = 30

    def get_queryset(self):
        qs = User.objects.select_related("business", "role").order_by("full_name")
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(business__name__icontains=q))
        return qs


class PlatformUserToggleView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user == request.user:
            messages.error(request, "You cannot deactivate your own account.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            log_action("user.active" if user.is_active else "user.deactivate", business=user.business, obj=user)
            messages.success(request, f"{user.full_name} is now {'active' if user.is_active else 'deactivated'}.")
        return redirect("platform:users")


class PlatformUserResetView(PlatformAdminRequiredMixin, View):
    """Send a password reset/activation link to a user."""

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        send_invitation(request, user)
        log_action("user.reset_link", business=user.business, obj=user)
        messages.success(request, f"Reset link sent to {user.email}.")
        return redirect("platform:users")


class PlatformAuditView(PlatformAdminRequiredMixin, ListView):
    template_name = "platform/audit.html"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related("business", "user")
        if self.request.GET.get("action"):
            qs = qs.filter(action__startswith=self.request.GET["action"])
        if self.request.GET.get("q"):
            q = self.request.GET["q"]
            qs = qs.filter(Q(message__icontains=q) | Q(user_label__icontains=q) | Q(ip_address__icontains=q))
        return qs


class PlatformInvoiceListView(PlatformAdminRequiredMixin, ListView):
    template_name = "platform/billing_list.html"
    context_object_name = "invoices"
    paginate_by = 30

    def get_queryset(self):
        qs = PlatformInvoice.objects.select_related("business").order_by("-issue_date", "-id")
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(Q(number__icontains=query) | Q(client_name__icontains=query) |
                           Q(client_email__icontains=query) | Q(business__name__icontains=query))
        return qs


class PlatformInvoiceCreateView(PlatformAdminRequiredMixin, FormView):
    template_name = "platform/billing_form.html"
    form_class = PlatformInvoiceForm

    def get_item_formset(self):
        if not hasattr(self, "_item_formset"):
            self._item_formset = PlatformInvoiceItemFormSet(self.request.POST or None, prefix="items")
        return self._item_formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("item_formset", self.get_item_formset())
        return context

    def form_valid(self, form):
        item_formset = self.get_item_formset()
        if not item_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))
        data = form.cleaned_data
        invoice = services.create_platform_invoice(
            user=self.request.user, client_business=data["client_business"], client_name=data["client_name"],
            client_email=data["client_email"], client_phone=data["client_phone"], client_address=data["client_address"],
            currency_code=data["currency_code"], currency_decimals=data["currency_decimals"],
            issue_date=data["issue_date"], due_date=data["due_date"],
            notes=data["notes"], terms=data["terms"], items=[row for row in item_formset.cleaned_data if row],
        )
        messages.success(self.request, f"Invoice {invoice.number} issued.")
        return redirect("platform:invoice_detail", pk=invoice.pk)


class PlatformInvoiceDetailView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "platform/billing_detail.html"

    def get_context_data(self, pk, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = get_object_or_404(
            PlatformInvoice.objects.select_related("business", "created_by").prefetch_related("items", "payments"),
            pk=pk,
        )
        context["payment_form"] = PlatformPaymentForm()
        from invoices.verification import verify_url

        context["verify_url"] = verify_url(context["invoice"], self.request)
        return context


class PlatformPaymentCreateView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        invoice = get_object_or_404(PlatformInvoice, pk=pk)
        form = PlatformPaymentForm(request.POST)
        if form.is_valid():
            try:
                payment = services.record_platform_payment(
                    invoice=invoice, user=request.user, **form.cleaned_data,
                )
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Payment recorded. Receipt {payment.number} issued.")
                return redirect("platform:receipt_detail", pk=payment.pk)
        else:
            for errors in form.errors.values():
                messages.error(request, " ".join(errors))
        return redirect("platform:invoice_detail", pk=pk)


class PlatformReceiptDetailView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "platform/billing_receipt.html"

    def get_context_data(self, pk, **kwargs):
        context = super().get_context_data(**kwargs)
        context["receipt"] = get_object_or_404(
            PlatformPayment.objects.select_related("invoice", "received_by", "invoice__business"), pk=pk,
        )
        from invoices.verification import verify_url

        context["verify_url"] = verify_url(context["receipt"], self.request)
        return context


class PlatformDocumentPdfView(PlatformAdminRequiredMixin, View):
    def get(self, request, kind, pk):
        from invoices import pdf
        from invoices.verification import verify_url

        if kind == "invoice":
            document = get_object_or_404(PlatformInvoice, pk=pk)
            content = pdf.render_platform_invoice(document, verify_url(document, request))
        elif kind == "receipt":
            document = get_object_or_404(PlatformPayment.objects.select_related("invoice"), pk=pk)
            content = pdf.render_platform_receipt(document, verify_url(document, request))
        else:
            return HttpResponse(status=404)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{document.number}.pdf"'
        return response


class PlatformInvoiceVoidView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        invoice = get_object_or_404(PlatformInvoice, pk=pk)
        if invoice.status == PlatformInvoice.VOID:
            messages.info(request, "This invoice is already void.")
        elif invoice.payments.exists():
            messages.error(request, "An invoice with recorded payments cannot be voided.")
        else:
            from invoices import verification

            invoice.status = PlatformInvoice.VOID
            invoice.signature = verification.compute_signature(invoice)
            invoice.save(update_fields=["status", "signature", "updated_at"])
            log_action("platform.invoice_void", user=request.user, message=f"{invoice.number} voided")
            messages.success(request, f"Invoice {invoice.number} voided.")
        return redirect("platform:invoice_detail", pk=invoice.pk)


class PlatformUserReset2FAView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        user.clear_two_factor()
        log_action("auth.2fa_reset", business=user.business, obj=user, message=f"Reset by platform admin {request.user}")
        messages.success(request, f"Two-factor reset for {user.full_name}.")
        return redirect("platform:users")
