from decimal import Decimal

from django.contrib import messages
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from audit.services import log_action
from core.mixins import BusinessFormMixin, BusinessQuerysetMixin, TenantRequiredMixin
from inventory.models import StockMovement
from inventory.services import manual_adjustment

from .forms import CategoryForm, ProductForm, UnitForm
from .models import Category, Product, Unit


class ProductListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "products.view"
    model = Product
    template_name = "products/list.html"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related("category", "unit", "stock")
        g = self.request.GET
        if g.get("q"):
            q = g["q"]
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q) | Q(brand__icontains=q))
        if g.get("category"):
            qs = qs.filter(category_id=g["category"])
        status = g.get("status")
        if status == "inactive":
            qs = qs.filter(is_active=False)
        elif status == "low":
            qs = qs.filter(is_active=True, stock__quantity__gt=0, stock__quantity__lte=F("reorder_level"))
        elif status == "out":
            qs = qs.filter(is_active=True, stock__quantity__lte=0)
        else:
            qs = qs.filter(is_active=True)
        sort = g.get("sort", "name")
        if sort in {"name", "-name", "selling_price", "-selling_price", "stock__quantity", "-stock__quantity"}:
            qs = qs.order_by(sort)
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["categories"] = Category.objects.filter(business=self.request.business)
        return ctx


class ProductCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "products.manage"
    model = Product
    form_class = ProductForm
    template_name = "products/form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["can_set_opening"] = self.request.user.has_business_perm("inventory.adjust")
        return kwargs

    def get_initial(self):
        unit = Unit.objects.filter(business=self.request.business, name="Piece").first()
        return {"unit": unit, "tax_rate": 0}

    def form_valid(self, form):
        response = super().form_valid(form)
        product = self.object
        log_action("product.create", obj=product, after={"name": product.name, "sku": product.sku,
                                                           "price": product.selling_price})
        opening = form.cleaned_data.get("opening_quantity")
        if opening:
            manual_adjustment(business=self.request.business, user=self.request.user, product=product,
                              movement_type=StockMovement.OPENING, quantity=opening, reason="Opening stock")
        messages.success(self.request, f"{product.display_name} added.")
        return response

    def get_success_url(self):
        return reverse("products:detail", args=[self.object.pk])


class ProductUpdateView(TenantRequiredMixin, BusinessQuerysetMixin, BusinessFormMixin, UpdateView):
    permission_required = "products.manage"
    model = Product
    form_class = ProductForm
    template_name = "products/form.html"

    def form_valid(self, form):
        before = {f: str(form.initial.get(f)) for f in form.changed_data if f != "image"}
        response = super().form_valid(form)
        log_action("product.update", obj=self.object, before=before,
                   after={f: str(form.cleaned_data.get(f)) for f in before})
        messages.success(self.request, "Product updated.")
        return response

    def get_success_url(self):
        return reverse("products:detail", args=[self.object.pk])


class ProductDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "products.view"
    model = Product
    template_name = "products/detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["movements"] = self.object.movements.select_related("created_by")[:10]
        ctx["variants"] = self.object.variants.all()
        return ctx


class ProductSearchApi(TenantRequiredMixin, View):
    """JSON product lookup used by the sale and purchase screens."""

    permission_required = ("products.view", "sales.create", "purchases.create")

    def get(self, request):
        q = request.GET.get("q", "").strip()
        qs = Product.objects.filter(business=request.business, is_active=True).select_related("unit", "stock")
        if q:
            exact = qs.filter(Q(barcode=q) | Q(sku__iexact=q))
            qs = exact if exact.exists() else qs.filter(
                Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q) | Q(brand__icontains=q)
                | Q(variant_label__icontains=q))
        results = [{
            "id": p.pk, "name": p.display_name, "sku": p.sku, "unit": p.unit.abbreviation,
            "price": str(p.selling_price), "wholesale": str(p.wholesale_price) if p.wholesale_price is not None else "",
            "cost": str(p.purchase_price), "stock": f"{p.stock.quantity.normalize():f}" if hasattr(p, "stock") else "0",
            "tax": str(p.effective_tax_rate()),
        } for p in qs.order_by("name")[:15]]
        return JsonResponse({"results": results})


class CatalogView(TenantRequiredMixin, TemplateView):
    permission_required = "products.view"
    template_name = "products/catalog.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["categories"] = Category.objects.filter(business=self.request.business)
        ctx["units"] = Unit.objects.filter(business=self.request.business)
        return ctx


class _CatalogForm(TenantRequiredMixin, BusinessFormMixin):
    permission_required = "products.manage"
    template_name = "components/form_page.html"
    success_url = reverse_lazy("products:categories")
    label = ""

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(title=f"{'Edit' if getattr(self, 'object', None) else 'New'} {self.label}",
                   cancel_url=self.success_url, submit_label="Save")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(f"{self.label}.save", obj=self.object)
        messages.success(self.request, f"{self.label.title()} saved.")
        return response


class CategoryCreateView(_CatalogForm, CreateView):
    model, form_class, label = Category, CategoryForm, "category"


class CategoryUpdateView(_CatalogForm, BusinessQuerysetMixin, UpdateView):
    model, form_class, label = Category, CategoryForm, "category"


class UnitCreateView(_CatalogForm, CreateView):
    model, form_class, label = Unit, UnitForm, "unit"


class UnitUpdateView(_CatalogForm, BusinessQuerysetMixin, UpdateView):
    model, form_class, label = Unit, UnitForm, "unit"


# ------------------------------------------------------------------ import / export
import csv

from django.http import HttpResponse

from reports.exporters import safe_text

from . import importer


class ProductExportView(TenantRequiredMixin, View):
    permission_required = "products.view"

    def get(self, request):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="products.csv"'
        response.write("\ufeff")
        w = csv.writer(response)
        w.writerow(importer.COLUMNS + ["on_hand"])
        qs = Product.objects.filter(business=request.business).select_related("category", "unit", "stock").order_by("name")
        for p in qs:
            w.writerow([safe_text(p.name), p.sku, p.barcode, safe_text(p.category.name if p.category else ""), safe_text(p.brand), p.unit.abbreviation,
                        p.purchase_price, p.selling_price, p.wholesale_price if p.wholesale_price is not None else "", p.reorder_level, "",
                        safe_text(p.description), p.stock.quantity])
        log_action("product.export", message=f"{qs.count()} products")
        return response


class ProductImportTemplateView(TenantRequiredMixin, View):
    permission_required = "products.manage"

    def get(self, request):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="product-import-template.csv"'
        w = csv.writer(response)
        w.writerow(importer.COLUMNS)
        w.writerow(["Cement 50kg", "", "", "Cement & Building", "Hima", "bag", "28500", "32000", "31000", "20", "100", ""])
        w.writerow(["Hammer 16oz", "HAM-16", "6001234567890", "Tools", "", "pc", "18000", "25000", "", "5", "30", ""])
        return response


class ProductImportView(TenantRequiredMixin, TemplateView):
    permission_required = "products.manage"
    template_name = "products/import.html"
    SESSION_KEY = "product_import"

    def post(self, request):
        business = request.business
        can_set_opening = request.user.has_business_perm("inventory.adjust")
        if request.POST.get("confirm"):
            pending = request.session.pop(self.SESSION_KEY, None)
            if not pending or pending.get("business") != business.pk:
                messages.error(request, "That import has expired. Please upload the file again.")
                return redirect("products:import")
            count = importer.commit(business, request.user, pending["rows"])
            log_action("product.import", message=f"{count} products imported")
            messages.success(request, f"{count} products imported.")
            return redirect("products:list")
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose a file to upload.")
            return redirect("products:import")
        try:
            rows = importer.read_rows(upload)
        except importer.ImportFileError as exc:
            messages.error(request, str(exc))
            return redirect("products:import")
        clean, errors, new_cats = importer.validate(business, rows, can_set_opening)
        if errors:
            request.session.pop(self.SESSION_KEY, None)
        else:
            request.session[self.SESSION_KEY] = {"business": business.pk, "rows": clean}
        ctx = self.get_context_data(errors=errors[:200], error_total=len(errors), ok_count=len(clean), preview=clean[:20],
                                    new_categories=new_cats, total=len(rows), can_commit=not errors)
        return self.render_to_response(ctx)
