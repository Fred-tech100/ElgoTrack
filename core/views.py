import logging

from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # pragma: no cover
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


def ready(request):
    """Readiness: database and cache both reachable."""
    from django.core.cache import cache

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        cache.set("health-check", "ok", 5)
        assert cache.get("health-check") == "ok"
    except Exception:
        logger.exception("Readiness check failed")
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ready"})


def bad_request(request, exception=None):
    return render(request, "errors/error.html", {"code": 400, "title": "Bad request",
                  "message": "We could not understand that request. Please go back and try again."}, status=400)


def permission_denied(request, exception=None):
    return render(request, "errors/error.html", {"code": 403, "title": "Access denied",
                  "message": "You do not have permission to do that. Ask a business owner to update your role if you need access."}, status=403)


def not_found(request, exception=None):
    return render(request, "errors/error.html", {"code": 404, "title": "Page not found",
                  "message": "The page you are looking for does not exist or you cannot access it."}, status=404)


def server_error(request):
    logger.exception("Unhandled server error at %s", request.path)
    return render(request, "errors/error.html", {"code": 500, "title": "Something went wrong",
                  "message": "An unexpected error occurred and has been logged. Please try again."}, status=500)

def home(request):
    """Public landing page for anonymous visitors; dashboard for signed-in users."""
    if request.user.is_authenticated:
        from reports.views import DashboardView
        return DashboardView.as_view()(request)
    return render(request, "landing/index.html")

def robots_txt(request):
    """Serves /robots.txt — rendered from templates/robots.txt."""
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    content = render_to_string("robots.txt", request=request)
    return HttpResponse(content, content_type="text/plain")    