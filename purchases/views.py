import json

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from contacts.models import Supplier
from core.exceptions import BusinessRuleError
from core.mixins import BusinessQuerysetMixin, TenantRequiredMixin
from core.utils import parse_date
from payments.models import PaymentMethod

from . import services
from .models import PurchaseOrder


class PurchaseListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "purchases.view"
    model = PurchaseOrder
    template_name = "purchases/list.html"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related("supplier")
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(number__icontains=g["q"]) | Q(supplier__name__icontains=g["q"]) | Q(supplier_reference__icontains=g["q"]))
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("supplier"):
            qs = qs.filter(supplier_id=g["supplier"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["suppliers"] = Supplier.objects.filter(business=self.request.business)
        return ctx


class PurchaseCreateView(TenantRequiredMixin, View):
    permission_required = "purchases.create"

    def get(self, request):
        return render(request, "purchases/new.html", {
            "suppliers": Supplier.objects.filter(business=request.business, is_active=True),
            "can_receive": request.user.has_business_perm("purchases.receive"),
            "initial_supplier": request.GET.get("supplier", ""),
        })

    def post(self, request):
        try:
            p = json.loads(request.body or "{}")
            supplier = get_object_or_404(Supplier, pk=p.get("supplier_id"), business=request.business)
            receive_now = bool(p.get("receive_now"))
            if receive_now and not request.user.has_business_perm("purchases.receive"):
                raise BusinessRuleError("You do not have permission to receive stock.")
            po = services.create_purchase(
                business=request.business, user=request.user, supplier=supplier, items=p.get("items", []),
                order_date=parse_date(p.get("order_date")), expected_date=parse_date(p.get("expected_date")),
                supplier_reference=p.get("supplier_reference", ""), notes=p.get("notes", ""), receive_now=receive_now,
            )
        except (BusinessRuleError, KeyError, ValueError, TypeError) as exc:
            msg = str(exc) if isinstance(exc, BusinessRuleError) else "The purchase could not be read. Please check the items."
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.success(request, f"Purchase {po.number} saved.")
        return JsonResponse({"ok": True, "redirect": f"/purchases/{po.pk}/"})


class PurchaseDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "purchases.view"
    model = PurchaseOrder
    template_name = "purchases/detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        po = self.object
        ctx.update(items=po.items.select_related("product"), receipts=po.receipts.prefetch_related("items"),
                   returns=po.returns.all(), payments=po.payments.select_related("method"),
                   methods=PaymentMethod.objects.filter(business=self.request.business, is_active=True))
        return ctx


class PurchaseReceiveView(TenantRequiredMixin, View):
    permission_required = "purchases.receive"

    def get(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
        return render(request, "purchases/receive.html", {"po": po, "items": po.items.select_related("product")})

    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
        lines = [{"item_id": i.pk, "quantity": request.POST.get(f"qty_{i.pk}", "0"),
                  "batch_number": request.POST.get(f"batch_{i.pk}", ""),
                  "expiry_date": parse_date(request.POST.get(f"expiry_{i.pk}"))} for i in po.items.all()]
        try:
            grn = services.receive_goods(po, request.user, lines, received_on=parse_date(request.POST.get("received_on")),
                                         supplier_reference=request.POST.get("supplier_reference", ""),
                                         notes=request.POST.get("notes", ""))
            messages.success(request, f"Goods received ({grn.number}). Stock updated.")
            return redirect("purchases:detail", pk=pk)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class PurchaseReturnView(TenantRequiredMixin, View):
    permission_required = "purchases.receive"

    def get(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
        rows = [(i, i.quantity_received - i.quantity_returned) for i in po.items.select_related("product")]
        return render(request, "purchases/return.html", {"po": po, "rows": rows})

    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
        lines = [{"item_id": i.pk, "quantity": request.POST.get(f"qty_{i.pk}", "0")} for i in po.items.all()]
        try:
            ret = services.return_goods(po, request.user, lines, request.POST.get("reason", ""))
            messages.success(request, f"Return {ret.number} recorded.")
            return redirect("purchases:detail", pk=pk)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class PurchaseCancelView(TenantRequiredMixin, View):
    permission_required = "purchases.cancel"

    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
        try:
            services.cancel_purchase(po, request.user, request.POST.get("reason", ""))
            messages.success(request, f"{po.number} cancelled.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("purchases:detail", pk=pk)
