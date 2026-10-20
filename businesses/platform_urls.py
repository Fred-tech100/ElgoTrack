from django.urls import path

from . import views

app_name = "platform"
urlpatterns = [
    path("", views.PlatformDashboardView.as_view(), name="dashboard"),
    path("businesses/", views.PlatformBusinessListView.as_view(), name="businesses"),
    path("businesses/new/", views.PlatformBusinessCreateView.as_view(), name="business_create"),
    path("businesses/<int:pk>/", views.PlatformBusinessDetailView.as_view(), name="business_detail"),
    path("businesses/<int:pk>/<str:action>/", views.PlatformBusinessActionView.as_view(), name="business_action"),
    path("users/", views.PlatformUserListView.as_view(), name="users"),
    path("users/<int:pk>/toggle/", views.PlatformUserToggleView.as_view(), name="user_toggle"),
    path("users/<int:pk>/reset/", views.PlatformUserResetView.as_view(), name="user_reset"),
    path("users/<int:pk>/reset-2fa/", views.PlatformUserReset2FAView.as_view(), name="user_reset_2fa"),
    path("audit/", views.PlatformAuditView.as_view(), name="audit"),
    path("billing/", views.PlatformInvoiceListView.as_view(), name="billing"),
    path("billing/new/", views.PlatformInvoiceCreateView.as_view(), name="invoice_create"),
    path("billing/<int:pk>/", views.PlatformInvoiceDetailView.as_view(), name="invoice_detail"),
    path("billing/<int:pk>/void/", views.PlatformInvoiceVoidView.as_view(), name="invoice_void"),
    path("billing/<int:pk>/payment/", views.PlatformPaymentCreateView.as_view(), name="invoice_payment"),
    path("billing/receipts/<int:pk>/", views.PlatformReceiptDetailView.as_view(), name="receipt_detail"),
    path("billing/documents/<str:kind>/<int:pk>/pdf/", views.PlatformDocumentPdfView.as_view(), name="billing_pdf"),
]
