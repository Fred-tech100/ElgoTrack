import logging
from datetime import datetime

from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from audit.services import log_action
from core.mixins import PlatformAdminRequiredMixin, TenantRequiredMixin
from core.ratelimit import rate_limit

from . import verification

logger = logging.getLogger(__name__)


def _count_hit(doc):
    type(doc).objects.filter(pk=doc.pk).update(verify_count=F("verify_count") + 1, last_verified_at=timezone.now())


@rate_limit("verify-public", limit=90, window=60, methods=("GET",))
def public_verify(request, token):
    """Public page behind the QR code: shows only what is printed on the document, never private details."""
    doc = verification.find_document(token=token)
    from django.shortcuts import render

    if doc is None:
        return render(request, "invoices/verify_public.html", {"found": False}, status=404)
    _count_hit(doc)
    return render(request, "invoices/verify_public.html", {"found": True, "d": verification.describe(doc)})


class ScanView(TenantRequiredMixin, TemplateView):
    permission_required = "invoices.view"
    template_name = "invoices/scan.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.urls import reverse

        context["lookup_url"] = reverse("verify:lookup")
        return context


class PlatformScanView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "platform/billing_scan.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.urls import reverse

        context["lookup_url"] = reverse("verify:platform_lookup")
        return context


class LookupView(TenantRequiredMixin, View):
    """Used by the in-app scanner: full details for the business's own documents, nothing for anyone else's."""

    permission_required = "invoices.view"

    @staticmethod
    def _payload(request, query):
        token, short = verification.extract_query(query)
        doc = verification.find_document(token=token, short_code=short)
        if doc is None:
            return {"status": "notfound", "message": "No document with this code exists. It may be forged or mistyped."}
        from businesses.models import PlatformInvoice, PlatformPayment

        if isinstance(doc, (PlatformInvoice, PlatformPayment)) or doc.business_id != request.business.id:
            log_action("document.verify_foreign", message=f"{doc.__class__.__name__} from another business scanned")
            return {"status": "foreign", "message": "This document was issued by a different business, not by yours."}
        _count_hit(doc)
        data = verification.describe(doc, detailed=True)
        log_action("document.verify", obj=doc, message=f"{data['kind']} {data['number']} -> {data['status']}")
        from django.urls import reverse

        data["open_url"] = reverse("invoices:invoice_detail" if data["kind"] == "invoice" else "invoices:receipt_detail", args=[doc.pk])
        return data

    def get(self, request):
        data = self._payload(request, request.GET.get("q", ""))
        if "date" in data:
            date_format = "%d %b %Y %H:%M" if isinstance(data["date"], datetime) else "%d %b %Y"
            data["date"] = data["date"].strftime(date_format)
        return JsonResponse(_jsonable(data))


class PlatformLookupView(PlatformAdminRequiredMixin, View):
    """Lookup only ElgonNova-issued platform billing documents in the platform scanner."""

    def get(self, request):
        from businesses.models import PlatformInvoice, PlatformPayment

        token, short = verification.extract_query(request.GET.get("q", ""))
        document = None
        for model in (PlatformInvoice, PlatformPayment):
            queryset = model.objects.select_related("invoice") if model is PlatformPayment else model.objects.all()
            document = queryset.filter(verify_token=token).first() if token else (
                queryset.filter(short_code=short).first() if short else None
            )
            if document is not None:
                break
        if document is None:
            found = verification.find_document(token=token, short_code=short)
            if found is not None:
                data = {"status": "foreign", "message": "This is an ElgoTrack business document, not an ElgonNova Technologies receipt or invoice."}
            else:
                data = {"status": "notfound", "message": "No ElgonNova Technologies invoice or receipt matches this code."}
            return JsonResponse(data)

        _count_hit(document)
        data = verification.describe(document, detailed=True)
        log_action("platform.document.verify", user=request.user,
                   message=f"{data['kind']} {data['number']} -> {data['status']}")
        from django.urls import reverse

        data["open_url"] = reverse(
            "platform:invoice_detail" if data["kind"] == "invoice" else "platform:receipt_detail",
            args=[document.pk],
        )
        data["date"] = data["date"].strftime("%d %b %Y")
        return JsonResponse(_jsonable(data))


def _jsonable(data):
    from decimal import Decimal

    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v

    return conv(data)
