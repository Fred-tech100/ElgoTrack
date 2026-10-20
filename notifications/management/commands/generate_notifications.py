from django.core.management.base import BaseCommand

from businesses.models import Business
from notifications.services import sync_notifications


class Command(BaseCommand):
    help = "Refresh low-stock, overdue, supplier-due, count and expiry notifications for all active businesses."

    def handle(self, *args, **opts):
        total = sum(sync_notifications(b) for b in Business.objects.filter(status="active"))
        self.stdout.write(self.style.SUCCESS(f"{total} new notification(s)."))
