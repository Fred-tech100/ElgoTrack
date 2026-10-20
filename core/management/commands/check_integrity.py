from django.core.management.base import BaseCommand, CommandError

from core.integrity import check_all


class Command(BaseCommand):
    help = "Verify stock levels and balances against their source records. Exit code 1 if anything disagrees."

    def handle(self, *args, **opts):
        bad = 0
        for business, problems in check_all().items():
            for p in problems:
                self.stderr.write(f"[{business.name}] {p}")
                bad += 1
        if bad:
            raise CommandError(f"{bad} integrity problem(s) found.")
        self.stdout.write(self.style.SUCCESS("All stock levels and balances agree with their source records."))
