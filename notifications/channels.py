"""Delivery channels. In-app is the database row itself; others are optional add-ons.

To add SMS or WhatsApp later: subclass ``Channel``, implement ``send`` and register it in ``CHANNELS``.
Enable by listing its key in the NOTIFICATION_CHANNELS setting (comma-separated env var).
"""
from django.conf import settings
from django.utils import timezone

from emailing.services import queue_email


class Channel:
    key = ""

    def send(self, notification, recipients):  # pragma: no cover - interface
        raise NotImplementedError


class InAppChannel(Channel):
    key = "in_app"

    def send(self, notification, recipients):
        return 0  # nothing to do: the Notification row is shown in the app


class EmailChannel(Channel):
    key = "email"

    def send(self, notification, recipients):
        emails = [u.email for u in recipients if u.email]
        if not emails:
            return 0
        context = {
            "business_name": notification.business.name,
            "title": notification.title,
            "body": notification.body,
            "heading_text": _heading(notification.kind),
            "detail_rows": _detail_rows(notification),
            "cta_label": "View inventory" if notification.kind in {"low_stock", "out_of_stock"} else "View account",
            "cta_url": f"{settings.ELGOTRACK_PUBLIC_URL}{notification.url}" if settings.ELGOTRACK_PUBLIC_URL and notification.url else "",
        }
        queued = 0
        for user in recipients:
            if user.email and queue_email(
                email_type=notification.kind,
                recipient=user.email,
                subject=f"[{notification.business.name}] {notification.title}",
                template="notification",
                context=context,
                business=notification.business,
                user=user,
                related=notification,
            ):
                queued += 1
        return queued


def _heading(kind):
    return {
        "low_stock": "Stock needs attention",
        "out_of_stock": "Out-of-stock alert",
        "credit_overdue": "Overdue customer credit",
    }.get(kind, "Business notification")


def _detail_rows(notification):
    if notification.kind in {"low_stock", "out_of_stock"}:
        from inventory.models import StockLevel

        try:
            stock = StockLevel.objects.select_related("product", "product__unit").get(
                business=notification.business,
                product_id=notification.dedupe_key.split(":", 1)[1],
            )
        except (StockLevel.DoesNotExist, ValueError, IndexError):
            return []
        product = stock.product
        rows = [
            ("Product", product.display_name),
            ("Current stock", f"{stock.quantity.normalize():f} {product.unit}"),
            ("Reorder level", f"{product.reorder_level.normalize():f} {product.unit}"),
        ]
        if product.sku:
            rows.insert(1, ("SKU", product.sku))
        return rows
    if notification.kind == "credit_overdue":
        from sales.models import Sale

        try:
            sale = Sale.objects.select_related("customer").get(
                business=notification.business,
                pk=notification.dedupe_key.split(":", 1)[1],
            )
        except (Sale.DoesNotExist, ValueError, IndexError):
            return []
        if not sale.due_date:
            return []
        return [
            ("Customer", sale.customer.name if sale.customer else "Customer"),
            ("Outstanding", f"{notification.business.currency_code} {sale.balance_due:,.{notification.business.currency_decimals}f}"),
            ("Due date", sale.due_date.strftime("%d %b %Y")),
            ("Days overdue", str((timezone.localdate() - sale.due_date).days)),
        ]
    return []


CHANNELS = {c.key: c() for c in (InAppChannel, EmailChannel)}


def active_channels():
    return [CHANNELS[k] for k in getattr(settings, "NOTIFICATION_CHANNELS", ["in_app"]) if k in CHANNELS]
