from dataclasses import dataclass
from datetime import timedelta

from django.db.models import F, Min
from django.utils import timezone

from .channels import active_channels
from .models import Notification


@dataclass
class Alert:
    key: str
    kind: str
    title: str
    body: str = ""
    url: str = ""
    severity: str = "warning"
    permission: str = ""


def _low_and_out(business):
    from inventory.models import StockLevel

    levels = StockLevel.objects.filter(business=business, product__is_active=True).select_related("product", "product__unit")
    for lv in levels.filter(quantity__gt=0, quantity__lte=F("product__reorder_level"))[:200]:
        p = lv.product
        yield Alert(f"low:{p.pk}", "low_stock", f"Low stock: {p.display_name}", f"{lv.quantity.normalize():f} {p.unit} left (reorder at {p.reorder_level.normalize():f}).",
                    f"/inventory/product/{p.pk}/", "warning", "inventory.view")
    for lv in levels.filter(quantity__lte=0, product__movements__isnull=False).distinct()[:200]:
        p = lv.product
        yield Alert(f"out:{p.pk}", "out_of_stock", f"Out of stock: {p.display_name}", "", f"/inventory/product/{p.pk}/", "danger", "inventory.view")


def _overdue(business):
    from sales.models import Sale

    today = timezone.localdate()
    for s in Sale.objects.filter(business=business, status="completed", balance_due__gt=0, due_date__lt=today).select_related("customer")[:200]:
        yield Alert(f"overdue:{s.pk}", "credit_overdue", f"Overdue: {s.number}", f"{s.customer.name if s.customer else 'Customer'} owes {s.balance_due:,.2f}, due {s.due_date:%d %b}.",
                    f"/sales/{s.pk}/", "danger", "payments.receive")


def _supplier_due(business):
    from purchases.models import PurchaseOrder

    today = timezone.localdate()
    qs = PurchaseOrder.objects.filter(business=business, received_value__gt=0, supplier__payment_terms_days__gt=0).annotate(first=Min("receipts__received_on")).select_related("supplier")
    for po in qs:
        if po.balance_due > 0 and po.first and po.first + timedelta(days=po.supplier.payment_terms_days) <= today + timedelta(days=3):
            due = po.first + timedelta(days=po.supplier.payment_terms_days)
            yield Alert(f"supdue:{po.pk}", "supplier_due", f"Pay {po.supplier.name} ({po.number})", f"{po.balance_due:,.2f} due {due:%d %b}.",
                        f"/purchases/{po.pk}/", "danger" if due < today else "warning", "payments.pay_supplier")


def _counts(business):
    from inventory.models import StockCount

    for c in StockCount.objects.filter(business=business, status="submitted"):
        yield Alert(f"count:{c.pk}", "count_pending", f"Stock count {c.number} needs approval", "Review the differences and approve or reject.",
                    f"/inventory/counts/{c.pk}/", "info", "inventory.approve")


def _expiry(business):
    from purchases.models import GoodsReceiptItem

    limit = timezone.localdate() + timedelta(days=30)
    for i in GoodsReceiptItem.objects.filter(receipt__business=business, expiry_date__isnull=False, expiry_date__lte=limit).select_related("purchase_item__product")[:200]:
        p = i.purchase_item.product
        expired = i.expiry_date < timezone.localdate()
        yield Alert(f"expiry:{i.pk}", "expiry", f"{'Expired' if expired else 'Expiring'}: {p.display_name}", f"Batch {i.batch_number or '—'} expires {i.expiry_date:%d %b %Y}.",
                    "/reports/r/stock-expiry/", "danger" if expired else "warning", "inventory.view")


GENERATORS = [_low_and_out, _overdue, _supplier_due, _counts, _expiry]


def sync_notifications(business):
    """Make the stored notifications match today's situation. Idempotent; safe to run often."""
    from accounts.models import User

    current = {a.key: a for gen in GENERATORS for a in gen(business)}
    now = timezone.now()
    existing = {n.dedupe_key: n for n in Notification.objects.filter(business=business)}
    for key, n in existing.items():
        if key not in current and n.resolved_at is None:
            n.resolved_at = now
            n.save(update_fields=["resolved_at", "updated_at"])
    created = []
    for key, a in current.items():
        n = existing.get(key)
        fields = dict(kind=a.kind, severity=a.severity, title=a.title[:160], body=a.body[:300], url=a.url, permission=a.permission)
        if n is None:
            created.append(Notification.objects.create(business=business, dedupe_key=key, **fields))
        else:
            reopened = n.resolved_at is not None
            for k, v in fields.items():
                setattr(n, k, v)
            n.resolved_at = None
            if reopened:
                n.reads.all().delete()
                n.emailed_at = None
                created.append(n)
            n.save()
    deliver(created)
    return len(created)


def deliver(notifications):
    from accounts.models import User

    channels = [c for c in active_channels() if c.key != "in_app"]
    if not channels:
        return
    for n in notifications:
        users = [u for u in User.objects.filter(business=n.business, is_active=True).select_related("role") if not n.permission or u.has_business_perm(n.permission)]
        for ch in channels:
            ch.send(n, users)


def visible_to(user):
    codes = list(user.perm_codes()) + [""]
    return Notification.objects.filter(business=user.business, resolved_at__isnull=True, permission__in=codes)


def unread_count(user):
    return visible_to(user).exclude(reads__user=user).count()
