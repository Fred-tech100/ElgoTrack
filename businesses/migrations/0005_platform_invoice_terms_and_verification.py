from django.db import migrations, models


def populate_verification_fields(apps, schema_editor):
    from invoices.verification import compute_signature, new_short_code, new_token

    database = schema_editor.connection.alias
    for model_name in ("PlatformInvoice", "PlatformPayment"):
        model = apps.get_model("businesses", model_name)
        for document in model.objects.using(database).all().iterator():
            document.verify_token = new_token()
            document.short_code = new_short_code()
            document.save(update_fields=["verify_token", "short_code"])
            document.signature = compute_signature(document)
            document.save(update_fields=["signature"])


class Migration(migrations.Migration):

    dependencies = [
        ("businesses", "0004_platforminvoice_currency_decimals_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="platforminvoice",
            name="terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="platforminvoice",
            name="verify_token",
            field=models.CharField(editable=False, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="platforminvoice",
            name="short_code",
            field=models.CharField(editable=False, max_length=14, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="platforminvoice",
            name="signature",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="platforminvoice",
            name="verify_count",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="platforminvoice",
            name="last_verified_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="platformpayment",
            name="verify_token",
            field=models.CharField(editable=False, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="platformpayment",
            name="short_code",
            field=models.CharField(editable=False, max_length=14, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="platformpayment",
            name="signature",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="platformpayment",
            name="verify_count",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="platformpayment",
            name="last_verified_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(populate_verification_fields, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="platforminvoice",
            name="verify_token",
            field=models.CharField(editable=False, max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="platforminvoice",
            name="short_code",
            field=models.CharField(editable=False, max_length=14, unique=True),
        ),
        migrations.AlterField(
            model_name="platforminvoice",
            name="signature",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AlterField(
            model_name="platformpayment",
            name="verify_token",
            field=models.CharField(editable=False, max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="platformpayment",
            name="short_code",
            field=models.CharField(editable=False, max_length=14, unique=True),
        ),
        migrations.AlterField(
            model_name="platformpayment",
            name="signature",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
    ]
