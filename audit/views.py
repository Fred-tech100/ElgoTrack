from django.db.models import Q
from django.views.generic import ListView

from core.mixins import TenantRequiredMixin
from core.utils import parse_date

from .models import AuditLog


class AuditLogListView(TenantRequiredMixin, ListView):
    permission_required = "audit.view"
    template_name = "audit/list.html"
    paginate_by = 40

    def get_queryset(self):
        qs = AuditLog.objects.filter(business=self.request.business).select_related("user")
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(object_repr__icontains=g["q"]) | Q(user_label__icontains=g["q"]) | Q(message__icontains=g["q"]))
        if g.get("action"):
            qs = qs.filter(action__startswith=g["action"])
        if parse_date(g.get("start")):
            qs = qs.filter(created_at__date__gte=parse_date(g["start"]))
        if parse_date(g.get("end")):
            qs = qs.filter(created_at__date__lte=parse_date(g["end"]))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["action_groups"] = ["auth", "product", "sale", "purchase", "stock", "payment", "expense", "user", "role", "business"]
        return ctx
