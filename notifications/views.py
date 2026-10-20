from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView

from core.mixins import TenantRequiredMixin

from .models import NotificationRead
from .services import visible_to


class NotificationListView(TenantRequiredMixin, ListView):
    template_name = "notifications/list.html"
    paginate_by = 30

    def get_queryset(self):
        return visible_to(self.request.user).order_by("-updated_at")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        read_ids = set(NotificationRead.objects.filter(user=self.request.user, notification__in=ctx["object_list"]).values_list("notification_id", flat=True))
        ctx["read_ids"] = read_ids
        return ctx


class MarkReadView(TenantRequiredMixin, View):
    def post(self, request, pk=None):
        qs = visible_to(request.user)
        if pk:
            qs = qs.filter(pk=pk)
        NotificationRead.objects.bulk_create([NotificationRead(notification=n, user=request.user) for n in qs.exclude(reads__user=request.user)], ignore_conflicts=True)
        target = request.POST.get("go")
        from django.utils.http import url_has_allowed_host_and_scheme
        if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            return redirect(target)
        return redirect("notifications:list")
