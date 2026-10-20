"""PostgreSQL only: trigram indexes so the 'contains' search on names, SKUs and phone numbers stays fast on large catalogues.

Django translates ``icontains`` to ``UPPER(col::text) LIKE UPPER(...)``, so the indexes are built on that exact expression.
The migration is a no-op on other databases and never fails the deployment: if the extension cannot be created
(insufficient privileges) it simply skips the indexes; search still works, just without the speed-up.
"""
from django.db import migrations

INDEXES = [
    ("products_product", "name"), ("products_product", "sku"), ("products_product", "barcode"), ("products_product", "brand"),
    ("contacts_customer", "name"), ("contacts_customer", "phone"), ("contacts_supplier", "name"),
]


def forwards(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SAVEPOINT trgm_setup")
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            for table, column in INDEXES:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {table}_{column}_trgm ON {table} USING gin (UPPER({column}::text) gin_trgm_ops)")
            cursor.execute("RELEASE SAVEPOINT trgm_setup")
        except Exception:  # pragma: no cover - depends on database privileges
            cursor.execute("ROLLBACK TO SAVEPOINT trgm_setup")


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table, column in INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {table}_{column}_trgm")


class Migration(migrations.Migration):
    dependencies = [("products", "0001_initial"), ("contacts", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
