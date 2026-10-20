from django.db import migrations, models

from invoices import verification


def backfill(apps, schema_editor):
    """Give every existing invoice and receipt a token, short code and signature."""
    Invoice = apps.get_model("invoices", "Invoice")
    Receipt = apps.get_model("invoices", "Receipt")
    for inv in Invoice.objects.select_related("sale"):
        token = verification.new_token()
        Invoice.objects.filter(pk=inv.pk).update(
            verify_token=token, short_code=verification.new_short_code(),
            signature=verification.sign_values("invoice", inv.business_id, inv.number, inv.issue_date.isoformat(), inv.sale.total, inv.sale.customer_id, token))
    from datetime import timezone as tz
    for r in Receipt.objects.all():
        token = verification.new_token()
        Receipt.objects.filter(pk=r.pk).update(
            verify_token=token, short_code=verification.new_short_code(),
            signature=verification.sign_values("receipt", r.business_id, r.number, r.issued_at.astimezone(tz.utc).strftime("%Y%m%d%H%M%S"), r.amount, r.customer_id, token))


class Migration(migrations.Migration):
    dependencies = [("invoices", "0001_initial"), ("sales", "0002_sale_client_token_sale_uniq_sale_client_token")]

    operations = []
    for model in ("invoice", "receipt"):
        operations += [
            migrations.AddField(model, "verify_token", models.CharField(max_length=32, null=True, editable=False)),
            migrations.AddField(model, "short_code", models.CharField(max_length=14, null=True, editable=False)),
            migrations.AddField(model, "signature", models.CharField(max_length=64, editable=False, blank=True, default=""), preserve_default=False),
            migrations.AddField(model, "verify_count", models.PositiveIntegerField(default=0, editable=False)),
            migrations.AddField(model, "last_verified_at", models.DateTimeField(null=True, blank=True, editable=False)),
        ]
    operations.append(migrations.RunPython(backfill, migrations.RunPython.noop))
    for model in ("invoice", "receipt"):
        operations += [
            migrations.AlterField(model, "verify_token", models.CharField(max_length=32, unique=True, editable=False)),
            migrations.AlterField(model, "short_code", models.CharField(max_length=14, unique=True, editable=False)),
        ]
