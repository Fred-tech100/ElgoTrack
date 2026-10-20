from django.urls import path

from . import views

app_name = "payments"
urlpatterns = [
    path("", views.PaymentListView.as_view(), name="list"),
    path("customer/new/", views.CustomerPaymentView.as_view(), name="customer_payment"),
    path("supplier/new/", views.SupplierPaymentView.as_view(), name="supplier_payment"),
    path("<int:pk>/reverse/", views.PaymentReverseView.as_view(), name="reverse"),
    path("methods/", views.MethodListView.as_view(), name="methods"),
    path("methods/new/", views.MethodCreateView.as_view(), name="method_create"),
    path("methods/<int:pk>/", views.MethodUpdateView.as_view(), name="method_edit"),
]
