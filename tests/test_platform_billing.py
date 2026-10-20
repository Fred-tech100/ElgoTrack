from decimal import Decimal

from django.urls import reverse

from accounts.models import User
from businesses.models import PlatformInvoice, PlatformPayment
from invoices import verification
from sales import services as sales_services

from .helpers import TenantTestCase


class PlatformBillingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser(email="root@elgonnova.test", password="Strong-Root-Pass-91",
                                                 full_name="Platform Admin")
        self.login(self.root)

    def invoice_data(self, **overrides):
        data = {
            "client_business": "",
            "client_name": "External Development Client",
            "client_email": "client@example.test",
            "client_phone": "+256700000100",
            "client_address": "Kampala",
            "currency_code": "UGX",
            "currency_decimals": "0",
            "issue_date": "2026-10-19",
            "due_date": "2026-11-18",
            "notes": "Website development milestone",
            "terms": "Payment due within 30 days.",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "25",
            "items-0-description": "Web application development",
            "items-0-quantity": "2",
            "items-0-unit_price": "500000",
            "items-0-tax_rate": "18",
            "items-1-description": "",
            "items-1-quantity": "",
            "items-1-unit_price": "",
            "items-1-tax_rate": "",
        }
        data.update(overrides)
        return data

    def test_external_invoice_custom_lines_partial_payment_receipt_and_pdfs(self):
        response = self.client.get(reverse("platform:billing"))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("platform:invoice_create"), self.invoice_data())
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        invoice = PlatformInvoice.objects.get()
        self.assertIsNone(invoice.business)
        self.assertEqual(invoice.client_name, "External Development Client")
        self.assertEqual(invoice.subtotal, Decimal("1000000.00"))
        self.assertEqual(invoice.tax_total, Decimal("180000.00"))
        self.assertEqual(invoice.total, Decimal("1180000.00"))
        self.assertEqual(invoice.items.count(), 1)
        self.assertContains(self.client.get(reverse("platform:invoice_detail", args=[invoice.pk])), "UGX 1,180,000")
        self.assertContains(self.client.get(reverse("platform:invoice_detail", args=[invoice.pk])), "Payment due within 30 days.")
        self.assertTrue(verification.integrity_ok(invoice))
        self.assertContains(self.client.get(reverse("verify:public", args=[invoice.verify_token])), invoice.number)
        invoice_lookup = self.client.get(reverse("verify:platform_lookup"), {"q": invoice.short_code}).json()
        self.assertEqual(invoice_lookup["status"], "genuine")
        self.assertEqual(invoice_lookup["payment_status"], "Unpaid")
        self.assertEqual(invoice_lookup["balance"], "1180000.00")
        self.assertEqual(self.client.get(reverse("verify:platform_scan")).status_code, 200)

        response = self.client.post(reverse("platform:invoice_payment", args=[invoice.pk]), {
            "amount": "400000", "paid_on": "2026-10-20", "method": "Mobile Money", "reference": "TX-42",
            "notes": "First instalment",
        })
        receipt = PlatformPayment.objects.get()
        self.assertRedirects(response, reverse("platform:receipt_detail", args=[receipt.pk]))
        self.assertTrue(verification.integrity_ok(receipt))
        receipt_lookup = self.client.get(reverse("verify:platform_lookup"), {"q": receipt.verify_token}).json()
        self.assertEqual(receipt_lookup["status"], "genuine")
        self.assertEqual(receipt_lookup["payment_status"], "Partially paid")
        self.assertEqual(receipt_lookup["balance"], "780000.00")
        self.assertEqual(receipt_lookup["items"][0]["name"], "Web application development")
        self.assertContains(self.client.get(reverse("verify:public", args=[receipt.verify_token])), receipt.number)
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal("400000.00"))
        self.assertEqual(invoice.balance_due, Decimal("780000.00"))

        self.assertContains(self.client.get(reverse("platform:invoice_detail", args=[invoice.pk])), invoice.number)
        receipt_page = self.client.get(reverse("platform:receipt_detail", args=[receipt.pk]))
        self.assertContains(receipt_page, "TX-42")
        self.assertContains(receipt_page, "Web application development")
        for kind, pk in (("invoice", invoice.pk), ("receipt", receipt.pk)):
            pdf = self.client.get(reverse("platform:billing_pdf", args=[kind, pk]))
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf["Content-Type"], "application/pdf")
            self.assertTrue(pdf.content.startswith(b"%PDF"))
            self.assertIn(b"/Subtype /Image", pdf.content)

        response = self.client.post(reverse("platform:invoice_payment", args=[invoice.pk]), {
            "amount": "800000", "paid_on": "2026-10-21", "method": "Bank",
        }, follow=True)
        self.assertContains(response, "Payment cannot exceed the invoice balance due.")
        self.assertEqual(PlatformPayment.objects.count(), 1)
        response = self.client.post(reverse("platform:invoice_payment", args=[invoice.pk]), {
            "amount": "780000", "paid_on": "2026-10-21", "method": "Bank",
        })
        invoice.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(invoice.payment_status, "paid")
        self.assertEqual(PlatformPayment.objects.count(), 2)
        receipt.refresh_from_db()
        self.assertEqual(receipt.balance_after, Decimal("780000.00"))
        self.assertContains(self.client.get(reverse("platform:receipt_detail", args=[receipt.pk])), "780,000")
        invoice.refresh_from_db()
        self.assertTrue(verification.integrity_ok(invoice))
        self.login(self.owner)
        tenant_lookup = self.client.get(reverse("verify:lookup"), {"q": invoice.verify_token}).json()
        self.assertEqual(tenant_lookup["status"], "foreign")
        self.assertNotIn("number", tenant_lookup)

    def test_platform_scanner_rejects_tenant_documents(self):
        sale = sales_services.create_sale(
            business=self.business,
            user=self.owner,
            items=[{"product_id": self.product.pk, "quantity": "1"}],
            payments=[{"method_id": self.cash.pk, "amount": "30000"}],
        )
        result = self.client.get(reverse("verify:platform_lookup"), {"q": sale.invoice.verify_token}).json()
        self.assertEqual(result["status"], "foreign")
        self.assertNotIn("number", result)

    def test_invoice_terms_are_signed(self):
        self.client.post(reverse("platform:invoice_create"), self.invoice_data())
        invoice = PlatformInvoice.objects.get()
        self.assertTrue(verification.integrity_ok(invoice))
        PlatformInvoice.objects.filter(pk=invoice.pk).update(terms="Changed after issue")
        invoice.refresh_from_db()
        self.assertFalse(verification.integrity_ok(invoice))
        result = self.client.get(reverse("verify:platform_lookup"), {"q": invoice.short_code}).json()
        self.assertEqual(result["status"], "tampered")

    def test_business_invoice_snapshots_business_contact(self):
        response = self.client.post(reverse("platform:invoice_create"), self.invoice_data(
            client_business=str(self.business.pk), client_name="", client_email="", client_phone="", client_address="",
            currency_code="USD", **{"items-0-tax_rate": "0"},
            currency_decimals="2",
        ))
        self.assertEqual(response.status_code, 302)
        invoice = PlatformInvoice.objects.get()
        self.assertEqual(invoice.business, self.business)
        self.assertEqual(invoice.client_name, self.business.name)
        self.assertEqual(invoice.client_email, self.business.email)
        self.assertEqual(invoice.client_phone, self.business.phone)

    def test_billing_is_platform_admin_only(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("platform:billing")).status_code, 403)
        self.assertEqual(self.client.post(reverse("platform:invoice_create"), self.invoice_data()).status_code, 403)

    def test_invoice_requires_a_client_and_at_least_one_line(self):
        response = self.client.post(reverse("platform:invoice_create"), self.invoice_data(
            client_name="", **{"items-0-description": "", "items-0-quantity": "", "items-0-unit_price": "",
                              "items-0-tax_rate": ""},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a client name or select an ElgoTrack business.")
        self.assertContains(response, "Please submit at least 1 form.")
        self.assertFalse(PlatformInvoice.objects.exists())

    def test_void_invoice_is_verifiable_and_paid_invoice_cannot_be_voided(self):
        response = self.client.post(reverse("platform:invoice_create"), self.invoice_data())
        invoice = PlatformInvoice.objects.get()
        response = self.client.post(reverse("platform:invoice_void", args=[invoice.pk]))
        self.assertRedirects(response, reverse("platform:invoice_detail", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PlatformInvoice.VOID)
        self.assertTrue(verification.integrity_ok(invoice))
        self.assertEqual(self.client.get(reverse("verify:platform_lookup"), {"q": invoice.short_code}).json()["status"], "void")

        self.client.post(reverse("platform:invoice_create"), self.invoice_data())
        paid_invoice = PlatformInvoice.objects.exclude(pk=invoice.pk).get()
        self.client.post(reverse("platform:invoice_payment", args=[paid_invoice.pk]), {
            "amount": "500000", "paid_on": "2026-10-20", "method": "Bank",
        })
        response = self.client.post(reverse("platform:invoice_void", args=[paid_invoice.pk]), follow=True)
        self.assertContains(response, "An invoice with recorded payments cannot be voided.")
        paid_invoice.refresh_from_db()
        self.assertEqual(paid_invoice.status, PlatformInvoice.ISSUED)
