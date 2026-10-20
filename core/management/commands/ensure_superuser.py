import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update the superuser from environment variables."

    def handle(self, *args, **options):
        User = get_user_model()

        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()

        if not (email and password):
            self.stdout.write(self.style.WARNING(
                "Skipping superuser creation: DJANGO_SUPERUSER_EMAIL or DJANGO_SUPERUSER_PASSWORD is missing."
            ))
            return

        # Determine the field used for login (email or username)
        user_field = getattr(User, "USERNAME_FIELD", "username")
        lookup = {user_field: email if user_field == "email" else email.split("@")[0]}

        user, created = User.objects.get_or_create(**lookup)

        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)

        # Ensure email is set if the model has the field
        if hasattr(user, "email") and user.email != email:
            user.email = email

        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Superuser created: {email}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Superuser updated (password reset): {email}"))