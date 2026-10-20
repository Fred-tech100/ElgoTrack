from django.urls import path

from . import views

app_name = "expenses"
urlpatterns = [
    path("", views.ExpenseListView.as_view(), name="list"),
    path("new/", views.ExpenseCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ExpenseDetailView.as_view(), name="detail"),
    path("<int:pk>/void/", views.ExpenseVoidView.as_view(), name="void"),
    path("<int:pk>/attachment/", views.ExpenseAttachmentView.as_view(), name="attachment"),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/", views.CategoryUpdateView.as_view(), name="category_edit"),
]
