from decimal import Decimal

from django import template
from django.utils.html import format_html
from django.utils.http import urlencode

register = template.Library()

STATUS_STYLES = {
    "paid": "success", "completed": "success", "received": "success", "approved": "success", "active": "success",
    "posted": "success", "issued": "success", "in": "success",
    "partial": "warning", "partially_received": "warning", "submitted": "warning", "pending": "warning",
    "ordered": "info", "draft": "neutral", "open": "info",
    "unpaid": "danger", "cancelled": "danger", "void": "danger", "rejected": "danger", "reversed": "danger",
    "suspended": "danger", "out": "warning", "overdue": "danger",
}


@register.simple_tag(takes_context=True)
def money(context, value, symbol=True):
    business = context.get("business")
    decimals = business.currency_decimals if business else 2
    code = business.currency_code if business else ""
    try:
        amount = Decimal(value or 0)
    except Exception:
        return value
    text = f"{amount:,.{decimals}f}"
    return f"{code} {text}" if symbol and code else text


@register.simple_tag
def platform_money(invoice, value):
    return f"{invoice.currency_code} {Decimal(value):,.{invoice.currency_decimals}f}"


@register.filter
def qty(value):
    if value is None or value == "":
        return ""
    try:
        value = Decimal(value)
    except Exception:
        return value
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


@register.simple_tag
def badge(status, label=None):
    key = str(status).lower()
    style = STATUS_STYLES.get(key, "neutral")
    text = label or str(status).replace("_", " ").capitalize()
    return format_html('<span class="badge badge-{}">{}</span>', style, text)


@register.simple_tag(takes_context=True)
def page_url(context, page):
    params = context["request"].GET.copy()
    params["page"] = page
    return "?" + urlencode(params, doseq=True)


@register.simple_tag(takes_context=True)
def keep_query(context, **overrides):
    params = context["request"].GET.copy()
    params.pop("page", None)
    for key, value in overrides.items():
        params[key] = value
    return urlencode(params, doseq=True)


@register.filter
def has_perm_code(user, code):
    return user.is_authenticated and user.has_business_perm(code)


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.filter
def mul(a, b):
    try:
        return Decimal(a) * Decimal(b)
    except Exception:
        return 0


@register.simple_tag
def qr_svg(data, scale=4):
    from core import qr

    return qr.svg_inline(data, scale=scale)


@register.filter
def comma(value, decimals=0):
    try:
        return f"{Decimal(value):,.{int(decimals)}f}"
    except Exception:
        return value
