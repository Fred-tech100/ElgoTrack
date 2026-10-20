"""Login throttling: lock out repeated failures per (email, IP) and per IP."""
from django.conf import settings
from django.core.cache import cache

IP_MULTIPLIER = 6


def _keys(email, ip):
    return f"login-fail:{(email or '').lower()}:{ip}", f"login-fail-ip:{ip}"


def is_locked(email, ip):
    pair_key, ip_key = _keys(email, ip)
    limit = settings.LOGIN_MAX_FAILURES
    return cache.get(pair_key, 0) >= limit or cache.get(ip_key, 0) >= limit * IP_MULTIPLIER


def register_failure(email, ip):
    for key in _keys(email, ip):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_FAILURE_WINDOW_SECONDS)


def clear(email, ip):
    cache.delete(_keys(email, ip)[0])
