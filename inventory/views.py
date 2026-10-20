from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from core.exceptions import BusinessRuleError
from core.mixins import BusinessFormMixin, TenantRequiredMixin
from core.utils import parse_date
from products.models import Category, Product

from . import services
from .forms import AdjustmentForm, CountStartForm
from .models import StockCount, StockMovement


class StockOverviewView(TenantRequiredMixin, ListView):
    permission_required = "inventory.view"
    template_name = "inventory/stock.html"
    paginate_by = 30

    def get_queryset(self):
        qs = Product.objects.filter(business=self.request.business, is_active=True).select_related("stock", "unit", "category")
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(name__icontains=g["q"]) | Q(sku__icontains=g["q"]) | Q(barcode__icontains=g["q"]))
        if g.get("category"):
            qs = qs.filter(category_id=g["category"])
        if g.get("status") == "low":
            qs = qs.filter(stock__quantity__gt=0, stock__quantity__lte=F("reorder_level"))
        elif g.get("status") == "out":
            qs = qs.filter(stock__quantity__lte=0)
        return qs.order_by("name")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["categories"] = Category.objects.filter(business=self.request.business)
        return ctx


class ProductLedgerView(TenantRequiredMixin, ListView):
    """Answers 'why is this product at this quantity?'"""

    permission_required = "inventory.view"
    template_name = "inventory/ledger.html"
    paginate_by = 40

    def get_queryset(self):
        self.product = get_object_or_404(Product, pk=self.kwargs["pk"], business=self.request.business)
        return StockMovement.objects.filter(business=self.request.business, product=self.product).select_related("created_by")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["product"] = self.product
        return ctx


class MovementListView(TenantRequiredMixin, ListView):
    permission_required = "inventory.view"
    template_name = "inventory/movements.html"
    paginate_by = 40

    def get_queryset(self):
        qs = StockMovement.objects.filter(business=self.request.business).select_related("product", "created_by")
        g = self.request.GET
        if g.get("type"):
            qs = qs.filter(movement_type=g["type"])
        if g.get("q"):
            qs = qs.filter(Q(product__name__icontains=g["q"]) | Q(product__sku__icontains=g["q"]) | Q(source_ref__icontains=g["q"]))
        if parse_date(g.get("start")):
            qs = qs.filter(created_at__date__gte=parse_date(g["start"]))
        if parse_date(g.get("end")):
            qs = qs.filter(created_at__date__lte=parse_date(g["end"]))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["types"] = StockMovement.TYPE_CHOICES
        return ctx


class AdjustStockView(TenantRequiredMixin, BusinessFormMixin, FormView):
    permission_required = "inventory.adjust"
    form_class = AdjustmentForm
    template_name = "inventory/adjust.html"

    def get_initial(self):
        return {"product": self.request.GET.get("product")}

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            services.manual_adjustment(
                business=self.request.business, user=self.request.user, product=d["product"],
                movement_type=d["movement_type"], quantity=d["quantity"], reason=d["reason"],
                batch_number=d["batch_number"], expiry_date=d["expiry_date"],
            )
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Stock updated for {d['product'].display_name}.")
        return redirect("inventory:ledger", pk=d["product"].pk)


# ------------------------------------------------------------------ stock counts
class CountListView(TenantRequiredMixin, ListView):
    permission_required = ("inventory.count", "inventory.approve")
    template_name = "inventory/count_list.html"
    paginate_by = 25

    def get_queryset(self):
        return StockCount.objects.filter(business=self.request.business).select_related("started_by", "category")


class CountStartView(TenantRequiredMixin, BusinessFormMixin, FormView):
    permission_required = "inventory.count"
    form_class = CountStartForm
    template_name = "components/form_page.html"
    extra_context = {"title": "Start a stock count", "submit_label": "Start count",
                     "intro": "The system records today's quantities as a snapshot. You then enter what you physically count."}

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["cancel_url"] = "/inventory/counts/"
        return ctx

    def form_valid(self, form):
        try:
            count = services.start_count(business=self.request.business, user=self.request.user,
                                         category=form.cleaned_data["category"], notes=form.cleaned_data["notes"])
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("inventory:count_detail", pk=count.pk)


class CountDetailView(TenantRequiredMixin, DetailView):
    permission_required = ("inventory.count", "inventory.approve")
    template_name = "inventory/count_detail.html"
    context_object_name = "count"

    def get_queryset(self):
        return StockCount.objects.filter(business=self.request.business).select_related("started_by", "reviewed_by", "submitted_by")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        lines = list(self.object.lines.select_related("product", "product__unit"))
        ctx["lines"] = lines
        counted = [l for l in lines if l.counted_quantity is not None]
        ctx["counted_total"] = len(counted)
        ctx["variance_lines"] = sum(1 for l in counted if l.variance != 0)
        ctx["variance_value"] = sum((l.variance_value for l in counted), Decimal("0"))
        return ctx


class CountActionView(TenantRequiredMixin, View):
    permission_required = ("inventory.count", "inventory.approve")

    def post(self, request, pk):
        count = get_object_or_404(StockCount, pk=pk, business=request.business)
        action = request.POST.get("action")
        user = request.user
        try:
            if action in ("save", "submit"):
                if not user.has_business_perm("inventory.count"):
                    return self.deny()
                entries = {}
                for line in count.lines.all():
                    raw = request.POST.get(f"counted_{line.pk}", "").strip()
                    try:
                        counted = Decimal(raw) if raw != "" else None
                    except InvalidOperation:
                        raise BusinessRuleError(f"'{raw}' is not a valid quantity.")
                    entries[line.pk] = (counted, request.POST.get(f"reason_{line.pk}", ""))
                services.save_count_entries(count, entries)
                if action == "submit":
                    services.submit_count(count, user)
                    messages.success(request, "Count submitted for approval.")
                else:
                    messages.success(request, "Progress saved.")
            elif action in ("approve", "reject"):
                if not user.has_business_perm("inventory.approve"):
                    return self.deny()
                note = request.POST.get("review_note", "")
                if action == "approve":
                    services.approve_count(count, user, note)
                    messages.success(request, "Count approved and stock adjusted.")
                else:
                    services.reject_count(count, user, note)
                    messages.success(request, "Count rejected. No stock was changed.")
            elif action == "cancel":
                services.cancel_count(count, user)
                messages.success(request, "Count cancelled.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("inventory:count_detail", pk=pk)

    def deny(self):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
