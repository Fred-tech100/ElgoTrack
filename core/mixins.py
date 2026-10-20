from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class TenantRequiredMixin(LoginRequiredMixin):
    """Requires a logged-in business user holding ``permission_required`` (any of a tuple)."""

    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser:
            return redirect("platform:dashboard")
        if request.business is None:
            raise PermissionDenied
        required = self.permission_required
        if required:
            codes = (required,) if isinstance(required, str) else tuple(required)
            if not any(request.user.has_business_perm(c) for c in codes):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class BusinessQuerysetMixin:
    """Restricts a view's queryset to the current business (tenant isolation)."""

    def get_queryset(self):
        return super().get_queryset().filter(business=self.request.business)


class BusinessFormMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs


class PlatformAdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class RuleErrorMixin:
    """Turns BusinessRuleError raised in form_valid into a form error message."""

    def form_valid(self, form):
        from .exceptions import BusinessRuleError

        try:
            return super().form_valid(form)
        except BusinessRuleError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


def flash_rule_error(request, exc):
    messages.error(request, str(exc))
