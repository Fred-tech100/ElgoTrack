from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.sitemaps import StaticSitemap
from core.views import robots_txt
handler400 = "core.views.bad_request"
handler403 = "core.views.permission_denied"
handler404 = "core.views.not_found"
handler500 = "core.views.server_error"

urlpatterns = [
    path("", include("core.urls")),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": {"static": StaticSitemap}}, name="sitemap"),
    path("accounts/", include("accounts.urls")),
    path("platform/", include("businesses.platform_urls")),
    path("settings/", include("businesses.urls")),
    path("products/", include("products.urls")),
    path("inventory/", include("inventory.urls")),
    path("", include("contacts.urls")),
    path("sales/", include("sales.urls")),
    path("purchases/", include("purchases.urls")),
    path("payments/", include("payments.urls")),
    path("expenses/", include("expenses.urls")),
    path("documents/", include("invoices.urls")),
    path("verify/", include("invoices.verify_urls")),
    path("reports/", include("reports.urls")),
    path("audit/", include("audit.urls")),
    path("notifications/", include("notifications.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
