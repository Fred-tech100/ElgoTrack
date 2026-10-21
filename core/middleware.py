import contextvars
import secrets

from django.shortcuts import render

_current_request = contextvars.ContextVar("current_request", default=None)


def get_current_request():
    return _current_request.get()


class CurrentRequestMiddleware:
    """Makes the request available to the audit layer (user and IP address)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


class BusinessMiddleware:
    """Attaches ``request.business`` and blocks users of non-active businesses."""

    EXEMPT_PREFIXES = ("/accounts/logout/", "/static/", "/media/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.business = None
        user = request.user
        if user.is_authenticated and user.business_id and not user.is_superuser:
            request.business = user.business
            if request.business.status != "active" and not request.path.startswith(self.EXEMPT_PREFIXES):
                return render(request, "errors/business_inactive.html", {"business": request.business}, status=403)
        return self.get_response(request)





class SecurityHeadersMiddleware:
    """Content-Security-Policy and related security headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(32)
        response = self.get_response(request)

        policy = [
            "default-src 'self'",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "script-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
        if request.path == "/" and not request.user.is_authenticated:
            policy[1] += " https://*.tawk.to"
            policy[2] += " https://*.tawk.to"
            policy[3] += f" 'nonce-{request.csp_nonce}' https://embed.tawk.to https://*.tawk.to"
            policy.extend([
                "connect-src 'self' https://*.tawk.to wss://*.tawk.to",
                "font-src 'self' data: https://*.tawk.to",
                "frame-src 'self' https://*.tawk.to",
            ])

        response["Content-Security-Policy"] = "; ".join(policy)
        response["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response["Cross-Origin-Opener-Policy"] = "same-origin"

        return response



class TwoFactorEnforcementMiddleware:
    """Sends users who must use two-factor authentication (platform admins, or all users of a business that
    requires it) to the set-up page until they have enabled it."""

    EXEMPT = ("/accounts/security/", "/accounts/logout/", "/static/", "/media/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not user.totp_enabled and not request.path.startswith(self.EXEMPT):
            from django.conf import settings

            required = (user.is_superuser and settings.REQUIRE_2FA_PLATFORM_ADMIN) or (
                getattr(request, "business", None) is not None and request.business.require_2fa)
            if required:
                from django.contrib import messages
                from django.shortcuts import redirect

                messages.warning(request, "Two-factor authentication is required. Set it up to continue.")
                return redirect("accounts:security")
        return self.get_response(request)
