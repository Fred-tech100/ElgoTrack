from django.urls import path

from . import views

app_name = "products"
urlpatterns = [
    path("", views.ProductListView.as_view(), name="list"),
    path("new/", views.ProductCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ProductDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ProductUpdateView.as_view(), name="edit"),
    path("export/", views.ProductExportView.as_view(), name="export"),
    path("import/", views.ProductImportView.as_view(), name="import"),
    path("import/template.csv", views.ProductImportTemplateView.as_view(), name="import_template"),
    path("api/search/", views.ProductSearchApi.as_view(), name="search"),
    path("catalog/", views.CatalogView.as_view(), name="categories"),
    path("catalog/category/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("catalog/category/<int:pk>/", views.CategoryUpdateView.as_view(), name="category_edit"),
    path("catalog/unit/new/", views.UnitCreateView.as_view(), name="unit_create"),
    path("catalog/unit/<int:pk>/", views.UnitUpdateView.as_view(), name="unit_edit"),
]
