from django.urls import path

from . import verify

app_name = "verify"
urlpatterns = [
    path("scan/", verify.ScanView.as_view(), name="scan"),
    path("lookup/", verify.LookupView.as_view(), name="lookup"),
    path("platform-scan/", verify.PlatformScanView.as_view(), name="platform_scan"),
    path("platform-lookup/", verify.PlatformLookupView.as_view(), name="platform_lookup"),
    path("<slug:token>/", verify.public_verify, name="public"),
]
