"""Grant newly introduced default permissions to the built-in roles of existing businesses.

Only ADDS permissions defined in core.permissions.DEFAULT_ROLES to roles that still carry a built-in name;
never removes anything and never touches custom roles.
"""
from django.core.management.base import BaseCommand

from accounts.models import Role, RolePermission
from core.permissions import DEFAULT_ROLES


class Command(BaseCommand):
    help = "Add missing default permissions to built-in roles."

    def handle(self, *args, **opts):
        added = 0
        for spec in DEFAULT_ROLES.values():
            for role in Role.objects.filter(name=spec["name"]).exclude(system_key="owner"):
                have = set(role.permissions.values_list("codename", flat=True))
                new = [c for c in spec["codes"] if c not in have]
                RolePermission.objects.bulk_create(RolePermission(role=role, codename=c) for c in new)
                added += len(new)
        self.stdout.write(self.style.SUCCESS(f"Added {added} permission(s)."))
