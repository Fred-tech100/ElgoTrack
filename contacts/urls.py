from django.urls import include, path

from . import views


def group(prefix, ns, list_v, create_v, detail_v, edit_v, pdf_kind):
    urls = [
        path("", list_v.as_view(), name="list"),
        path("new/", create_v.as_view(), name="create"),
        path("<int:pk>/", detail_v.as_view(), name="detail"),
        path("<int:pk>/edit/", edit_v.as_view(), name="edit"),
        path("<int:pk>/statement.pdf", views.StatementPdfView.as_view(kind=pdf_kind), name="statement_pdf"),
    ]
    if ns == "customers":
        urls.append(path("<int:pk>/statement/email/", views.CustomerStatementEmailView.as_view(), name="statement_email"))
    return path(prefix, include((urls, ns), namespace=ns))


urlpatterns = [
    group("customers/", "customers", views.CustomerListView, views.CustomerCreateView, views.CustomerDetailView, views.CustomerUpdateView, "customer"),
    group("suppliers/", "suppliers", views.SupplierListView, views.SupplierCreateView, views.SupplierDetailView, views.SupplierUpdateView, "supplier"),
]
