import os

from django.contrib import messages
from django.http import FileResponse, Http404
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.exceptions import BusinessRuleError
from core.mixins import BusinessFormMixin, BusinessQuerysetMixin, TenantRequiredMixin
from core.utils import date_range

from . import services
from .forms import ExpenseCategoryForm, ExpenseForm
from .models import Expense, ExpenseCategory


class ExpenseListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "expenses.view"
    model = Expense
    template_name = "expenses/list.html"
    paginate_by = 30

    def filtered(self):
        qs = super().get_queryset().select_related("category", "method", "recorded_by")
        g = self.request.GET
        start, end = date_range(self.request, 30)
        qs = qs.filter(expense_date__gte=start, expense_date__lte=end)
        if g.get("category"):
            qs = qs.filter(category_id=g["category"])
        if g.get("q"):
            qs = qs.filter(Q(description__icontains=g["q"]) | Q(number__icontains=g["q"]))
        return qs

    def get_queryset(self):
        return self.filtered()

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        start, end = date_range(self.request, 30)
        ctx.update(start=start, end=end,
                   categories=ExpenseCategory.objects.filter(business=self.request.business),
                   total=self.filtered().filter(status="posted").aggregate(t=Sum("amount"))["t"] or 0)
        return ctx


class ExpenseCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "expenses.create"
    model = Expense
    form_class = ExpenseForm
    template_name = "components/form_page.html"
    extra_context = {"title": "Record expense", "cancel_url": reverse_lazy("expenses:list"), "submit_label": "Save expense"}

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            expense = services.record_expense(
                business=self.request.business, user=self.request.user, category=d["category"], amount=d["amount"],
                method=d["method"], description=d["description"], expense_date=d["expense_date"],
                attachment=d.get("attachment"))
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Expense {expense.number} recorded.")
        return redirect("expenses:detail", pk=expense.pk)


class ExpenseDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "expenses.view"
    model = Expense
    template_name = "expenses/detail.html"


class ExpenseVoidView(TenantRequiredMixin, View):
    permission_required = "expenses.void"

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk, business=request.business)
        try:
            services.void_expense(expense, request.user, request.POST.get("reason", ""))
            messages.success(request, "Expense voided.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("expenses:detail", pk=pk)


class ExpenseAttachmentView(TenantRequiredMixin, View):
    """Receipts are private financial documents: never exposed as public /media/ URLs."""

    permission_required = "expenses.view"

    def get(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk, business=request.business)
        if not expense.attachment:
            raise Http404
        response = FileResponse(expense.attachment.open("rb"))
        response["Content-Disposition"] = f'inline; filename="{expense.number}{os.path.splitext(expense.attachment.name)[1].lower()}"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


class CategoryListView(TenantRequiredMixin, BusinessQuerysetMixin, ListView):
    permission_required = "expenses.view"
    model = ExpenseCategory
    template_name = "expenses/categories.html"


class CategoryCreateView(TenantRequiredMixin, BusinessFormMixin, CreateView):
    permission_required = "expenses.create"
    model = ExpenseCategory
    form_class = ExpenseCategoryForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("expenses:categories")
    extra_context = {"title": "New expense category", "cancel_url": reverse_lazy("expenses:categories"), "submit_label": "Save"}


class CategoryUpdateView(TenantRequiredMixin, BusinessQuerysetMixin, BusinessFormMixin, UpdateView):
    permission_required = "expenses.create"
    model = ExpenseCategory
    form_class = ExpenseCategoryForm
    template_name = "components/form_page.html"
    success_url = reverse_lazy("expenses:categories")
    extra_context = {"title": "Edit expense category", "cancel_url": reverse_lazy("expenses:categories"), "submit_label": "Save"}
