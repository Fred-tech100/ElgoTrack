from django.urls import path

from . import views

app_name = "inventory"
urlpatterns = [
    path("", views.StockOverviewView.as_view(), name="stock"),
    path("movements/", views.MovementListView.as_view(), name="movements"),
    path("adjust/", views.AdjustStockView.as_view(), name="adjust"),
    path("product/<int:pk>/", views.ProductLedgerView.as_view(), name="ledger"),
    path("counts/", views.CountListView.as_view(), name="count_list"),
    path("counts/new/", views.CountStartView.as_view(), name="count_start"),
    path("counts/<int:pk>/", views.CountDetailView.as_view(), name="count_detail"),
    path("counts/<int:pk>/action/", views.CountActionView.as_view(), name="count_action"),
]
