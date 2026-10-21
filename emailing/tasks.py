import logging

from celery import shared_task
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.utils import timezone

from .models import EmailDelivery
from .providers import EmailProviderError, get_provider
from .services import mark_delivery_failed, render_email

logger = logging.getLogger(__name__)


def _deliver_email(delivery_id, payload, task=None):
    try:
        delivery = EmailDelivery.objects.get(pk=delivery_id)
    except EmailDelivery.DoesNotExist:
        logger.error("Email delivery record %s no longer exists.", delivery_id)
        return
    if delivery.status == EmailDelivery.SENT:
        return

    delivery.status = EmailDelivery.SENDING
    delivery.attempts += 1
    delivery.error_message = ""
    delivery.save(update_fields=["status", "attempts", "error_message", "updated_at"])
    try:
        html, text = render_email(payload["template"], payload["context"])
        message_id = get_provider().send(
            subject=payload["subject"],
            recipient=payload["recipient"],
            html_content=html,
            text_content=text,
            attachments=payload.get("attachments", []),
        )
    except EmailProviderError as exc:
        if task is not None and exc.retryable and task.request.retries < task.max_retries:
            delivery.status = EmailDelivery.PENDING
            delivery.error_message = str(exc)[:300]
            delivery.save(update_fields=["status", "error_message", "updated_at"])
            raise task.retry(exc=exc, countdown=60 * (2 ** task.request.retries))
        mark_delivery_failed(delivery, str(exc))
        logger.error("Email delivery %s failed: %s", delivery.pk, exc)
        return
    except (TemplateDoesNotExist, TemplateSyntaxError, KeyError, ValueError) as exc:
        mark_delivery_failed(delivery, f"Email content could not be rendered ({type(exc).__name__}).")
        logger.exception("Email delivery %s could not be rendered.", delivery.pk)
        return
    except Exception as exc:
        mark_delivery_failed(delivery, f"Unexpected email delivery error ({type(exc).__name__}).")
        logger.error("Email delivery %s failed unexpectedly (%s).", delivery.pk, type(exc).__name__)
        return

    delivery.status = EmailDelivery.SENT
    delivery.provider_message_id = message_id[:150]
    delivery.error_message = ""
    delivery.save(update_fields=["status", "provider_message_id", "error_message", "updated_at"])
    if delivery.related_type == "notifications.notification":
        from notifications.models import Notification

        Notification.objects.filter(pk=delivery.related_id, emailed_at__isnull=True).update(emailed_at=timezone.now())


@shared_task(bind=True, max_retries=3)
def deliver_email(self, delivery_id, payload):
    return _deliver_email(delivery_id, payload, task=self)


def deliver_email_synchronously(delivery_id, payload):
    _deliver_email(delivery_id, payload)
