import json

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from contacts.models import Customer
from core.exceptions import BusinessRuleError
from core.mixins import BusinessQuerysetMixin, TenantRequiredMixin
from core.utils import D, parse_date
from payments.models import PaymentMethod
from payments.services import receive_customer_payment

from . import services
from .models import Sale


class SaleListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "sales.view"
    model = Sale
    template_name = "sales/list.html"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related("customer", "created_by")
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(number__icontains=g["q"]) | Q(customer__name__icontains=g["q"]) | Q(invoice__number__icontains=g["q"]))
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("payment"):
            qs = qs.filter(payment_status=g["payment"])
        if g.get("overdue"):
            qs = qs.filter(status="completed", balance_due__gt=0, due_date__lt=timezone.localdate())
        if parse_date(g.get("start")):
            qs = qs.filter(sale_date__date__gte=parse_date(g["start"]))
        if parse_date(g.get("end")):
            qs = qs.filter(sale_date__date__lte=parse_date(g["end"]))
        return qs


class SaleCreateView(TenantRequiredMixin, View):
    permission_required = "sales.create"

    def get(self, request):
        return render(request, "sales/new.html", {
            "customers": Customer.objects.filter(business=request.business, is_active=True),
            "methods": PaymentMethod.objects.filter(business=request.business, is_active=True),
            "can_discount": request.user.has_business_perm("sales.discount"),
            "default_credit_days": request.business.default_credit_days,
        })

    def post(self, request):
        try:
            payload = json.loads(request.body or "{}")
            customer = None
            if payload.get("customer_id"):
                customer = get_object_or_404(Customer, pk=payload["customer_id"], business=request.business)
            sale = services.create_sale(
                business=request.business, user=request.user, items=payload.get("items", []),
                customer=customer, payments=payload.get("payments", []),
                due_date=parse_date(payload.get("due_date")), notes=payload.get("notes", ""),
                client_token=str(payload.get("client_token", ""))[:64],
            )
        except IntegrityError:  # two identical submissions raced: return the one that won
            sale = Sale.objects.filter(business=request.business, client_token=str(payload.get("client_token", ""))[:64]).first()
            if sale is None:
                return JsonResponse({"ok": False, "error": "The sale could not be saved. Please try again."}, status=400)
        except (BusinessRuleError, KeyError, ValueError, TypeError) as exc:
            message = str(exc) if isinstance(exc, BusinessRuleError) else "The sale could not be read. Please check the items."
            return JsonResponse({"ok": False, "error": message}, status=400)
        messages.success(request, f"Sale {sale.number} recorded.")
        return JsonResponse({"ok": True, "redirect": f"/sales/{sale.pk}/"})


class SaleDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "sales.view"
    model = Sale
    template_name = "sales/detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        sale = self.object
        ctx.update(
            items=sale.items.select_related("product"),
            payments=sale.payments.select_related("method", "receipt").order_by("paid_on", "id"),
            returns=sale.returns.select_related("created_by"),
            methods=PaymentMethod.objects.filter(business=self.request.business, is_active=True),
        )
        return ctx


class SalePaymentView(TenantRequiredMixin, View):
    permission_required = "payments.receive"

    def post(self, request, pk):
        sale = get_object_or_404(Sale, pk=pk, business=request.business)
        try:
            method = get_object_or_404(PaymentMethod, pk=request.POST.get("method"), business=request.business)
            if sale.customer is None:
                raise BusinessRuleError("This sale has no customer account to apply a payment to.")
            receipt, _ = receive_customer_payment(
                business=request.business, user=request.user, customer=sale.customer, sale=sale,
                amount=D(request.POST.get("amount"), 0), method=method, reference=request.POST.get("reference", ""),
            )
            messages.success(request, f"Payment recorded. Receipt {receipt.number}.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("sales:detail", pk=pk)


class SaleReturnView(TenantRequiredMixin, View):
    permission_required = "sales.return"

    def get_sale(self, request, pk):
        return get_object_or_404(Sale, pk=pk, business=request.business)

    def get(self, request, pk):
        sale = self.get_sale(request, pk)
        items = [(i, i.quantity - i.returned_quantity()) for i in sale.items.select_related("product")]
        return render(request, "sales/return.html", {
            "sale": sale, "rows": items,
            "methods": PaymentMethod.objects.filter(business=request.business, is_active=True)})

    def post(self, request, pk):
        sale = self.get_sale(request, pk)
        lines = [{"item_id": i.pk, "quantity": request.POST.get(f"qty_{i.pk}", "0")} for i in sale.items.all()]
        try:
            method = PaymentMethod.objects.filter(business=request.business, pk=request.POST.get("method")).first()
            ret = services.create_sale_return(sale=sale, user=request.user, lines=lines,
                                              reason=request.POST.get("reason", ""),
                                              refund_amount=request.POST.get("refund_amount") or 0,
                                              refund_method=method)
            messages.success(request, f"Return {ret.number} recorded.")
            return redirect("sales:detail", pk=pk)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class SaleCancelView(TenantRequiredMixin, View):
    permission_required = "sales.cancel"

    def post(self, request, pk):
        sale = get_object_or_404(Sale, pk=pk, business=request.business)
        try:
            services.cancel_sale(sale, request.user, request.POST.get("reason", ""))
            messages.success(request, f"Sale {sale.number} cancelled and stock restored.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("sales:detail", pk=pk)
