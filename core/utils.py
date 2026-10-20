from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings

from .exceptions import BusinessRuleError

TWO = Decimal("0.01")
THREE = Decimal("0.001")


def D(value, default=None):
    """Parse a value to Decimal, raising a friendly error when invalid."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise BusinessRuleError(f"'{value}' is not a valid number.")


def q2(value):
    return Decimal(value).quantize(TWO, ROUND_HALF_UP)


def q3(value):
    return Decimal(value).quantize(THREE, ROUND_HALF_UP)


def client_ip(request):
    if request is None:
        return None
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def parse_date(value, default=None):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def date_range(request, default_days=30):
    """Return (start, end) dates from GET ``start``/``end`` params."""
    today = date.today()
    end = parse_date(request.GET.get("end"), today)
    start = parse_date(request.GET.get("start"), end - timedelta(days=default_days))
    if start > end:
        start, end = end, start
    return start, end
