"""Small cache-based rate limiter for unauthenticated endpoints (registration, password reset)."""
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse

from .utils import client_ip


def rate_limit(key, limit=5, window=3600, methods=("POST",)):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method in methods:
                cache_key = f"rl:{key}:{client_ip(request)}"
                try:
                    count = cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, 1, window)
                    count = 1
                if count > limit:
                    return HttpResponse("Too many requests. Please try again later.", status=429)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
