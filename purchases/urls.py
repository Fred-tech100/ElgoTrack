from django.urls import path

from . import views

app_name = "purchases"
urlpatterns = [
    path("", views.PurchaseListView.as_view(), name="list"),
    path("new/", views.PurchaseCreateView.as_view(), name="create"),
    path("<int:pk>/", views.PurchaseDetailView.as_view(), name="detail"),
    path("<int:pk>/receive/", views.PurchaseReceiveView.as_view(), name="receive"),
    path("<int:pk>/return/", views.PurchaseReturnView.as_view(), name="return"),
    path("<int:pk>/cancel/", views.PurchaseCancelView.as_view(), name="cancel"),
]
