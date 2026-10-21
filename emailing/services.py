import base64
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, transaction
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.urls import reverse

from .models import EmailDelivery

logger = logging.getLogger(__name__)
LOGO_PATH = "/static/images/landing/logo.png"


def queue_email(*, email_type, recipient, subject, template, context, business=None, user=None,
                related=None, attachments=()):
    """Persist a delivery record and send only after the caller's transaction commits."""
    recipient = (recipient or "").strip()
    try:
        validate_email(recipient)
    except ValidationError:
        logger.warning("Email %s was not queued because its recipient address is missing or invalid.", email_type)
        return None

    encoded_attachments = []
    for name, content, content_type in attachments:
        if len(content) > 8 * 1024 * 1024:
            logger.warning("Email %s was not queued because an attachment exceeds 8 MB.", email_type)
            return None
        encoded_attachments.append({
            "name": name,
            "content": base64.b64encode(content).decode("ascii"),
            "contentType": content_type,
        })

    related_type, related_id = "", ""
    if related is not None:
        related_type = related._meta.label_lower[:60]
        related_id = str(related.pk)[:80]
    try:
        delivery = EmailDelivery.objects.create(
            email_type=email_type,
            recipient=recipient,
            business=business,
            user=user,
            related_type=related_type,
            related_id=related_id,
        )
    except DatabaseError:
        logger.exception("Could not record email delivery for event %s.", email_type)
        return None

    payload = {
        "email_type": email_type,
        "recipient": recipient,
        "subject": subject[:255],
        "template": template,
        "context": context,
        "attachments": encoded_attachments,
    }

    def enqueue():
        if settings.EMAIL_DELIVERY_MODE == "sync":
            from .tasks import deliver_email_synchronously

            deliver_email_synchronously(delivery.pk, payload)
            return
        try:
            from .tasks import deliver_email

            deliver_email.delay(delivery.pk, payload)
        except Exception as exc:
            EmailDelivery.objects.filter(pk=delivery.pk).update(
                status=EmailDelivery.FAILED,
                error_message=f"Could not enqueue email ({type(exc).__name__}).",
                updated_at=timezone.now(),
            )
            logger.error("Could not enqueue email delivery %s (%s).", delivery.pk, type(exc).__name__)

    transaction.on_commit(enqueue, robust=True)
    return delivery


def render_email(template, context):
    global_context = {
        "brand_name": settings.DEFAULT_FROM_NAME,
        "support_email": settings.SUPPORT_EMAIL,
        "logo_url": f"{settings.ELGOTRACK_PUBLIC_URL}{LOGO_PATH}" if settings.ELGOTRACK_PUBLIC_URL else "",
    }
    context = {**global_context, **context}
    html = render_to_string(f"emails/{template}.html", context)
    text = render_to_string(f"emails/{template}.txt", context).strip() or strip_tags(html)
    return html, text


def mark_delivery_failed(delivery, message):
    delivery.status = EmailDelivery.FAILED
    delivery.error_message = message[:300]
    delivery.save(update_fields=["status", "error_message", "updated_at"])


def send_welcome(user, *, awaiting_approval=False):
    app_url = f"{settings.ELGOTRACK_PUBLIC_URL}{reverse('accounts:login')}" if settings.ELGOTRACK_PUBLIC_URL else ""
    subject = (
        "Thank you for registering with ElgoTrack"
        if awaiting_approval
        else "Your ElgoTrack account is ready"
    )
    return queue_email(
        email_type="welcome",
        recipient=user.email,
        subject=subject,
        template="welcome",
        context={
            "first_name": user.get_short_name(),
            "business_name": user.business.name if user.business_id else "",
            "awaiting_approval": awaiting_approval,
            "cta_label": "Sign in" if awaiting_approval else "Sign in to your account",
            "cta_url": app_url,
        },
        business=user.business,
        user=user,
        related=user,
    )


def send_password_changed(user):
    app_url = f"{settings.ELGOTRACK_PUBLIC_URL}{reverse('accounts:login')}" if settings.ELGOTRACK_PUBLIC_URL else ""
    return queue_email(
        email_type="password_changed",
        recipient=user.email,
        subject="Your ElgoTrack password was changed",
        template="password_changed",
        context={
            "first_name": user.get_short_name(),
            "changed_at": timezone.localtime().strftime("%d %b %Y at %H:%M %Z"),
            "cta_label": "Sign in",
            "cta_url": app_url,
        },
        business=user.business,
        user=user,
        related=user,
    )
