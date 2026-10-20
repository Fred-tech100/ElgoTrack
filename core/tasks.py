"""Background jobs (Celery). They are plain functions too, so cron + manage.py works without Celery."""
import logging

try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(fn=None, **kw):
        def wrap(f):
            f.delay = lambda *a, **k: f(*a, **k)
            return f
        return wrap(fn) if fn else wrap

logger = logging.getLogger(__name__)


@shared_task
def generate_notifications():
    from businesses.models import Business
    from notifications.services import sync_notifications

    return sum(sync_notifications(b) for b in Business.objects.filter(status="active"))


@shared_task
def nightly_integrity_check():
    """Log and raise an in-app alert if any figure disagrees with its source records."""
    from notifications.models import Notification

    from .integrity import check_all

    total = 0
    for business, problems in check_all().items():
        key = "integrity"
        if problems:
            total += len(problems)
            logger.error("Integrity problems for %s: %s", business.name, "; ".join(problems[:10]))
            Notification.objects.update_or_create(
                business=business, dedupe_key=key,
                defaults=dict(kind="system", severity="danger", title="Data integrity check found problems",
                              body=f"{len(problems)} figure(s) disagree with their source records. Contact support.",
                              permission="settings.manage", resolved_at=None))
        else:
            Notification.objects.filter(business=business, dedupe_key=key, resolved_at__isnull=True).update(resolved_at=__import__("django.utils.timezone", fromlist=["now"]).now())
    return total


@shared_task
def clear_expired_sessions():
    from django.core.management import call_command

    call_command("clearsessions")
