from django.contrib import messages
from django.core.cache import cache
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView

from accounts.models import User
from audit.services import log_action
from contacts.models import Customer, Supplier
from core.mixins import TenantRequiredMixin
from inventory.models import StockMovement
from payments.models import PaymentMethod
from products.models import Category, Product

from . import charts, engine, services
from .exporters import EXPORTERS
from .models import SavedReport


class DashboardView(TenantRequiredMixin, TemplateView):
    template_name = "reports/dashboard.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        user, business = self.request.user, self.request.business
        show_finance = user.has_business_perm("finance.view")
        ctx["m"] = services.dashboard_metrics(business, include_finance=show_finance)
        key = f"dash-charts:{business.pk}:{int(show_finance)}"
        ctx["charts"] = cache.get(key) or cache.get_or_set(key, lambda: charts.dashboard_charts(business, show_finance), 60)
        ctx["show_finance"] = show_finance
        steps = services.onboarding_steps(business) if user.has_business_perm("settings.manage") else []
        ctx["onboarding"] = steps if steps and not all(done for _, done, _ in steps) else []
        ctx["onboarding_done"] = sum(1 for _, d, _ in steps if d)
        return ctx


class FinancialSummaryView(TenantRequiredMixin, TemplateView):
    permission_required = "finance.view"
    template_name = "reports/summary.html"

    def get_context_data(self, **kw):
        from core.utils import date_range

        ctx = super().get_context_data(**kw)
        start, end = date_range(self.request, 30)
        summary = services.financial_summary(self.request.business, start, end)
        ctx.update(start=start, end=end, s=summary, expense_bars=charts.hbars([(c["category__name"], c["total"], "") for c in summary["expenses_by_category"]], charts.EXPENSE),
                   customers=services.top_balances(self.request.business, "customer"),
                   suppliers=services.top_balances(self.request.business, "supplier"))
        return ctx


def filter_options(business):
    return {
        "categories": Category.objects.filter(business=business),
        "products": Product.objects.filter(business=business, is_active=True).only("id", "name", "variant_label"),
        "suppliers": Supplier.objects.filter(business=business),
        "customers": Customer.objects.filter(business=business),
        "users": User.objects.filter(business=business),
        "methods": PaymentMethod.objects.filter(business=business),
    }


def export_response(result, business, fmt, slug):
    func, ctype, ext = EXPORTERS[fmt]
    response = HttpResponse(func(result, business), content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{slugify(slug)}-{result.subtitle[:10].replace(" ", "")}.{ext}"'
    return response


class ReportIndexView(TenantRequiredMixin, TemplateView):
    permission_required = ("reports.view", "finance.view", "reports.builder")
    template_name = "reports/index.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        user = self.request.user
        groups = {}
        for r in engine.REPORTS:
            if user.has_business_perm(r.permission):
                groups.setdefault(r.group, []).append(r)
        ctx["groups"] = groups
        ctx["saved"] = SavedReport.objects.filter(business=self.request.business) if user.has_business_perm("reports.builder") else []
        return ctx


class ReportRunView(TenantRequiredMixin, TemplateView):
    permission_required = ("reports.view", "finance.view")  # coarse gate; each report then checks its own permission
    template_name = "reports/run.html"

    def get_report(self):
        report = engine.REPORT_MAP.get(self.kwargs["key"])
        if report is None:
            raise Http404
        if not self.request.user.has_business_perm(report.permission):
            raise PermissionDenied
        return report

    def get(self, request, *args, **kwargs):
        report = self.get_report()
        f = engine.Filters.from_request(request, request.business)
        result = report.build(request.business, f)
        fmt = request.GET.get("export")
        if fmt:
            if fmt not in EXPORTERS:
                raise Http404
            log_action("report.export", message=f"{report.title} ({fmt})")
            return export_response(result, request.business, fmt, report.key)
        ctx = self.get_context_data(report=report, result=result, f=f, **kwargs)
        return self.render_to_response(ctx)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        report = kw["report"]
        ctx.update(filter_options(self.request.business))
        ctx["active_filters"] = report.filters
        ctx["type_choices"] = TYPE_CHOICES
        return ctx


TYPE_CHOICES = [(v, l) for v, l in StockMovement.TYPE_CHOICES]


class BuilderView(TenantRequiredMixin, TemplateView):
    permission_required = "reports.builder"
    template_name = "reports/builder.html"
    DATASETS = [("sales", "Sales"), ("purchases", "Purchases (goods received)"), ("stock", "Stock movements"),
                ("payments", "Payments"), ("expenses", "Expenses")]

    def get(self, request, *args, **kwargs):
        business = request.business
        dataset = request.GET.get("dataset", "")
        result, error = None, None
        f = engine.Filters.from_request(request, business)
        if dataset:
            if dataset not in dict(self.DATASETS):
                raise Http404
            if dataset in ("payments", "expenses") and not request.user.has_business_perm("finance.view"):
                raise PermissionDenied
            group = f.group_by
            if group and group not in engine.dataset_groups(dataset):
                error, group = "That grouping is not available for this data. Showing rows instead.", ""
            result = (engine.sales_grouped(business, f, group, "Custom sales report") if dataset == "sales" and group else
                      engine.grouped(dataset, business, f, group, f"Custom {dataset} report") if group else
                      engine.detail(dataset, business, f, f"Custom {dataset} report"))
            fmt = request.GET.get("export")
            if fmt in EXPORTERS:
                log_action("report.export", message=f"Custom {dataset} ({fmt})")
                return export_response(result, business, fmt, f"custom-{dataset}")
        ctx = self.get_context_data(result=result, f=f, dataset=dataset, error=error, **kwargs)
        return self.render_to_response(ctx)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(filter_options(self.request.business))
        ctx.update(active_filters=["date", "category", "product", "supplier", "customer", "user", "method", "movement_type"],
                   datasets=self.DATASETS, type_choices=TYPE_CHOICES, saved=SavedReport.objects.filter(business=self.request.business),
                   groups={d: engine.dataset_groups(d) for d, _ in self.DATASETS}, query=self.request.GET.urlencode())
        return ctx

    def post(self, request):
        name = request.POST.get("name", "").strip()[:80]
        params = {k: v for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken", "name", "start", "end", "export") and v}
        if not name or "dataset" not in params:
            messages.error(request, "Give the report a name and choose its data first.")
            return redirect("reports:builder")
        try:
            SavedReport.objects.update_or_create(business=request.business, name=name,
                                                 defaults={"params": params, "created_by": request.user})
        except IntegrityError:
            messages.error(request, "Could not save the report.")
        else:
            log_action("report.save", message=name)
            messages.success(request, f"Saved report “{name}”. Dates are relative to the day you open it.")
        from urllib.parse import urlencode
        return redirect("/reports/builder/?" + urlencode(params))


class SavedReportView(TenantRequiredMixin, View):
    permission_required = "reports.builder"

    def get(self, request, pk):
        from urllib.parse import urlencode
        saved = get_object_or_404(SavedReport, pk=pk, business=request.business)
        params = dict(saved.params)
        params.setdefault("days", "30")
        return redirect("/reports/builder/?" + urlencode(params))

    def post(self, request, pk):
        saved = get_object_or_404(SavedReport, pk=pk, business=request.business)
        log_action("report.delete", message=saved.name)
        saved.delete()
        messages.success(request, "Saved report removed.")
        return redirect("reports:builder")


class DailySummaryView(TenantRequiredMixin, TemplateView):
    permission_required = "finance.view"
    template_name = "reports/daily.html"

    def get_context_data(self, **kw):
        from core.utils import parse_date

        ctx = super().get_context_data(**kw)
        day = parse_date(self.request.GET.get("date"), timezone.localdate())
        b = self.request.business
        summary = services.daily_summary(b, day)
        ctx.update(day=day, d=summary, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1) if day < timezone.localdate() else None,
                   mix=charts.donut([(m["method__name"], m["total"]) for m in summary["cash"]["by_method"]]), hours=charts.hourly_sales(b, day),
                   expense_bars=charts.hbars([(c["category__name"], c["total"], "") for c in summary["expenses"]["by_category"]], charts.EXPENSE),
                   top_bars=charts.hbars([(t["description"], t["net"], f"{t['q'].normalize():f} sold") for t in summary["top"]], charts.SALES))
        return ctx
