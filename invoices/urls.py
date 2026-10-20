from django.urls import path

from . import views

app_name = "invoices"
urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("invoice/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoice/<int:pk>/pdf/", views.InvoicePdfView.as_view(), name="invoice_pdf"),
    path("invoice/<int:pk>/email/", views.EmailInvoiceView.as_view(), name="invoice_email"),
    path("receipt/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt_detail"),
    path("receipt/<int:pk>/pdf/", views.ReceiptPdfView.as_view(), name="receipt_pdf"),
    path("receipt/<int:pk>/email/", views.EmailReceiptView.as_view(), name="receipt_email"),
]
