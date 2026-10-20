from django.db.models import Q
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, ListView

from core.mixins import BusinessQuerysetMixin, TenantRequiredMixin
from emailing.services import queue_email

from . import pdf, verification
from .models import Invoice, Receipt


class DocumentListView(TenantRequiredMixin, ListView):
    permission_required = "invoices.view"
    template_name = "invoices/list.html"
    paginate_by = 30

    def get_queryset(self):
        self.kind = self.request.GET.get("kind", "invoices")
        model = Receipt if self.kind == "receipts" else Invoice
        qs = model.objects.filter(business=self.request.business)
        q = self.request.GET.get("q")
        if self.kind == "receipts":
            qs = qs.select_related("customer")
            if q:
                qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
        else:
            qs = qs.select_related("sale", "sale__customer")
            if q:
                qs = qs.filter(Q(number__icontains=q) | Q(sale__number__icontains=q) | Q(sale__customer__name__icontains=q))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["kind"] = self.kind
        return ctx


class InvoiceDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "invoices.view"
    model = Invoice
    template_name = "invoices/invoice_detail.html"

    def get_queryset(self):
        return super().get_queryset().select_related("sale", "sale__customer")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["verify_url"] = verification.verify_url(self.object, self.request)
        return ctx


class ReceiptDetailView(TenantRequiredMixin, BusinessQuerysetMixin, DetailView):
    permission_required = "invoices.view"
    model = Receipt
    template_name = "invoices/receipt_detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["payments"] = self.object.payments.select_related("method", "sale")
        ctx["verify_url"] = verification.verify_url(self.object, self.request)
        return ctx


class _PdfView(TenantRequiredMixin, View):
    permission_required = "invoices.view"
    model = None
    renderer = None

    def get(self, request, pk):
        obj = get_object_or_404(self.model, pk=pk, business=request.business)
        response = HttpResponse(type(self).renderer(obj, verification.verify_url(obj, request)), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{obj.number}.pdf"'
        return response


class InvoicePdfView(_PdfView):
    model = Invoice
    renderer = staticmethod(pdf.render_invoice)


class ReceiptPdfView(_PdfView):
    model = Receipt
    renderer = staticmethod(pdf.render_receipt)


class EmailDocumentView(TenantRequiredMixin, View):
    permission_required = "invoices.view"
    model = None
    document_type = ""

    def post(self, request, pk):
        document = get_object_or_404(self.model, pk=pk, business=request.business)
        customer = document.sale.customer if isinstance(document, Invoice) else document.customer
        if customer is None or customer.business_id != request.business.pk or not customer.email:
            messages.error(request, "This customer does not have an email address on file.")
            return self._redirect(document)

        verify_url = verification.verify_url(document, request)
        if isinstance(document, Invoice):
            attachment = pdf.render_invoice(document, verify_url)
            document_date = document.issue_date.strftime("%d %b %Y")
            amount = document.sale.total
            payment_status = document.sale.get_payment_status_display()
        else:
            attachment = pdf.render_receipt(document, verify_url)
            document_date = document.issued_at.strftime("%d %b %Y %H:%M")
            amount = document.amount
            payment_status = ""
        business = document.business
        delivery = queue_email(
            email_type=self.document_type.lower(),
            recipient=customer.email,
            subject=f"{self.document_type} {document.number} from {business.name}",
            template="document",
            context={
                "first_name": customer.name,
                "business_name": business.name,
                "document_type": self.document_type,
                "document_number": document.number,
                "document_date": document_date,
                "document_amount": f"{business.currency_code} {amount:,.{business.currency_decimals}f}",
                "payment_status": payment_status,
                "cta_label": "Verify document",
                "cta_url": verify_url,
            },
            business=business,
            user=request.user,
            related=document,
            attachments=[(f"{document.number}.pdf", attachment, "application/pdf")],
        )
        if delivery:
            messages.success(request, f"{self.document_type} email queued for {customer.email}.")
        else:
            messages.error(request, "The email could not be queued. Check the customer email and try again.")
        return self._redirect(document)

    def _redirect(self, document):
        name = "invoices:invoice_detail" if self.model is Invoice else "invoices:receipt_detail"
        return redirect(name, pk=document.pk)


class EmailInvoiceView(EmailDocumentView):
    model = Invoice
    document_type = "Invoice"


class EmailReceiptView(EmailDocumentView):
    model = Receipt
    document_type = "Receipt"
