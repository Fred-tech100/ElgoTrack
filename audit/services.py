import json

from django.core.serializers.json import DjangoJSONEncoder

from core.middleware import get_current_request
from core.utils import client_ip

from .models import AuditLog


def _safe(data):
    if data is None:
        return None
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder, default=str))


def log_action(action, *, business=None, user=None, obj=None, before=None, after=None, message=""):
    """Record an audit entry. User, business and IP are taken from the current request when omitted."""
    request = get_current_request()
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    if business is None:
        business = getattr(obj, "business", None) if obj is not None else None
        if business is None and request is not None:
            business = getattr(request, "business", None)
        if business is None and user is not None:
            business = getattr(user, "business", None)
    return AuditLog.objects.create(
        business=business,
        user=user,
        user_label=(str(user) if user else "")[:200],
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "") or ""),
        object_repr=str(obj)[:200] if obj is not None else "",
        before=_safe(before),
        after=_safe(after),
        message=message[:300],
        ip_address=client_ip(request),
    )
