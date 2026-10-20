from django.urls import path

from . import views

app_name = "sales"
urlpatterns = [
    path("", views.SaleListView.as_view(), name="list"),
    path("new/", views.SaleCreateView.as_view(), name="create"),
    path("<int:pk>/", views.SaleDetailView.as_view(), name="detail"),
    path("<int:pk>/payment/", views.SalePaymentView.as_view(), name="payment"),
    path("<int:pk>/return/", views.SaleReturnView.as_view(), name="return"),
    path("<int:pk>/cancel/", views.SaleCancelView.as_view(), name="cancel"),
]
