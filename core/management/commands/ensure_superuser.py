import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update the superuser from environment variables."

    def handle(self, *args, **options):
        User = get_user_model()

        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()
        full_name = os.environ.get("DJANGO_SUPERUSER_FULL_NAME", "").strip() or "Platform Administrator"

        if not (email and password):
            self.stdout.write(self.style.WARNING(
                "Skipping superuser: DJANGO_SUPERUSER_EMAIL or DJANGO_SUPERUSER_PASSWORD is missing."
            ))
            return

        # Use the model's own manager method — it handles email normalisation,
        # sets is_staff/is_superuser, and calls save() safely.
        user = User.objects.filter(email=email.lower()).first()

        if user:
            # Update existing user: reset password, ensure admin flags,
            # fill in full_name if it's empty.
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.set_password(password)
            if not user.full_name:
                user.full_name = full_name
            user.save()
            self.stdout.write(self.style.SUCCESS(
                f"Superuser updated (password reset): {email}"
            ))
            return

        # Create new user via create_superuser — this correctly sets
        # is_staff/is_superuser BEFORE the initial save, satisfying the
        # business_user_needs_business check constraint.
        user = User.objects.create_superuser(
            email=email,
            password=password,
            full_name=full_name,
        )
        self.stdout.write(self.style.SUCCESS(f"Superuser created: {email}"))