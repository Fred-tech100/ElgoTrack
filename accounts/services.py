from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.permissions import DEFAULT_ROLES

from .models import Role, RolePermission


def create_default_roles(business):
    roles = {}
    for key, spec in DEFAULT_ROLES.items():
        role = Role.objects.create(
            business=business, name=spec["name"], description=spec["description"],
            system_key=key if key == "owner" else "",
        )
        if key != "owner":
            RolePermission.objects.bulk_create(RolePermission(role=role, codename=c) for c in spec["codes"])
        roles[key] = role
    return roles


def set_role_permissions(role, codes):
    role.permissions.exclude(codename__in=codes).delete()
    existing = set(role.permissions.values_list("codename", flat=True))
    RolePermission.objects.bulk_create(RolePermission(role=role, codename=c) for c in set(codes) - existing)


def send_invitation(request, user, inviter=None):
    """Email a set-password link (also used for account activation)."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    business = user.business.name if user.business_id else "ElgonNova"
    from emailing.services import queue_email

    queue_email(
        email_type="business_invitation",
        recipient=user.email,
        subject=f"You’ve been invited to {business} on ElgoTrack",
        template="invitation",
        context={
            "first_name": user.get_short_name(),
            "business_name": business,
            "inviter_name": inviter.get_short_name() if inviter else "",
            "role_name": user.role.name if user.role_id else "",
            "cta_label": "Accept invitation",
            "cta_url": request.build_absolute_uri(path),
            "expiry_days": max(1, (settings.PASSWORD_RESET_TIMEOUT + 86399) // 86400),
        },
        business=user.business,
        user=user,
        related=user,
    )
