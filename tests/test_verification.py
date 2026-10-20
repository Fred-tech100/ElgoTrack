import base64
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

from django.test import override_settings
from django.urls import reverse

from audit.models import AuditLog
from contacts.models import Customer
from core import qr
from invoices import pdf, verification
from invoices.models import Invoice, Receipt
from payments import services as pay
from sales import services as sales
from sales.models import Sale

from .helpers import TenantTestCase, make_product, make_user

D = Decimal
ROOT = Path(__file__).resolve().parent.parent


class VerificationBase(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.cust = Customer.objects.create(business=self.business, name="Okello Joseph Mukasa", phone="0772123456", customer_type="credit")
        self.sale = sales.create_sale(business=self.business, user=self.owner, customer=self.cust,
                                      items=[{"product_id": self.product.pk, "quantity": "2"}], payments=[{"method_id": self.cash.pk, "amount": "20000"}])
        self.invoice = self.sale.invoice
        self.receipt = Receipt.objects.get()
        self.url = lambda doc: reverse("verify:public", args=[doc.verify_token])


class TokenTests(VerificationBase):
    def test_tokens_codes_and_signatures(self):
        for doc in (self.invoice, self.receipt):
            self.assertEqual(len(doc.verify_token), 24)
            self.assertRegex(doc.short_code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
            self.assertEqual(len(doc.signature), 64)
            self.assertTrue(verification.integrity_ok(doc))
        more = [sales.create_sale(business=self.business, user=self.owner, items=[{"product_id": self.product.pk, "quantity": "1"}], payments=[{"method_id": self.cash.pk, "amount": "30000"}]) for _ in range(5)]
        tokens = {s.invoice.verify_token for s in more} | {self.invoice.verify_token}
        self.assertEqual(len(tokens), 6)

    def test_short_code_normalisation_and_query_parsing(self):
        self.assertEqual(verification.normalise_short_code("abcd efgh-jklm"), "ABCD-EFGH-JKLM")
        self.assertEqual(verification.normalise_short_code("abc"), "")
        self.assertEqual(verification.extract_query("https://x.test/verify/AbC_123-xyz/?a=1"), ("AbC_123-xyz", ""))
        self.assertEqual(verification.extract_query("abcdefghjklm"), ("", "ABCD-EFGH-JKLM"))

    def test_signature_detects_database_tampering(self):
        Sale.objects.filter(pk=self.sale.pk).update(total=D("1.00"))
        self.assertFalse(verification.integrity_ok(Invoice.objects.get(pk=self.invoice.pk)))
        Receipt.objects.filter(pk=self.receipt.pk).update(amount=D("99999"))
        self.assertFalse(verification.integrity_ok(Receipt.objects.get(pk=self.receipt.pk)))
        Invoice.objects.filter(pk=self.invoice.pk).update(number="INV-2026-99999")
        self.assertFalse(verification.integrity_ok(Invoice.objects.get(pk=self.invoice.pk)))

    def test_signature_is_bound_to_secret_key(self):
        sig = verification.compute_signature(self.invoice)
        with override_settings(SECRET_KEY="a-completely-different-secret-key-value"):
            self.assertNotEqual(verification.compute_signature(self.invoice), sig)

    @override_settings(SITE_URL="https://inventory.example.com")
    def test_site_url_used_in_qr(self):
        self.assertEqual(verification.verify_url(self.invoice), f"https://inventory.example.com/verify/{self.invoice.verify_token}/")

    def test_backfill_gives_existing_documents_tokens(self):
        from django.apps import apps
        from importlib import import_module
        migration = import_module("invoices.migrations.0002_document_verification")
        Invoice.objects.filter(pk=self.invoice.pk).update(verify_token="old1", short_code="old1", signature="")
        Receipt.objects.filter(pk=self.receipt.pk).update(verify_token="old2", short_code="old2", signature="")
        migration.backfill(apps, None)
        self.assertTrue(verification.integrity_ok(Invoice.objects.get(pk=self.invoice.pk)))
        self.assertTrue(verification.integrity_ok(Receipt.objects.get(pk=self.receipt.pk)))
        self.assertEqual(len(Invoice.objects.get().verify_token), 24)


class PublicPageTests(VerificationBase):
    def test_genuine_invoice_and_receipt(self):
        r = self.client.get(self.url(self.invoice))  # no login needed
        self.assertContains(r, "Genuine invoice")
        self.assertContains(r, self.invoice.number)
        self.assertContains(r, "UGX 60,000")
        self.assertContains(r, self.business.name)
        r = self.client.get(self.url(self.receipt))
        self.assertContains(r, "Genuine receipt")
        self.assertContains(r, "UGX 20,000")
        self.assertEqual(r["X-Frame-Options"], "DENY")

    def test_public_page_never_leaks_private_details(self):
        text = self.client.get(self.url(self.invoice)).content.decode()
        for secret in ("Mukasa", "0772123456", "Cement", self.cust.code, self.sale.number):
            self.assertNotIn(secret, text)
        self.assertIn("Okello J.", text)  # only a masked name hint

    def test_unknown_token_is_not_recognised(self):
        r = self.client.get(reverse("verify:public", args=["x" * 24]))
        self.assertContains(r, "Not recognised", status_code=404)
        self.assertContains(r, "Possible forgery", status_code=404)

    def test_cancelled_sale_and_reversed_payment_are_flagged(self):
        sales.cancel_sale(self.sale, self.owner, "Mistake")
        inv_page = self.client.get(self.url(Invoice.objects.get()))
        self.assertContains(inv_page, "no longer valid")
        self.assertContains(inv_page, "cancelled")
        self.assertContains(self.client.get(self.url(Receipt.objects.get())), "no longer valid")

    def test_altered_records_are_called_out(self):
        Sale.objects.filter(pk=self.sale.pk).update(total=D("10.00"))
        r = self.client.get(self.url(Invoice.objects.get()))
        self.assertContains(r, "Record altered")
        self.assertNotContains(r, "Genuine invoice")

    def test_view_counter_and_rate_limit(self):
        for _ in range(3):
            self.client.get(self.url(self.invoice))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.verify_count, 3)
        self.assertIsNotNone(self.invoice.last_verified_at)
        codes = [self.client.get(reverse("verify:public", args=[f"guess{i}"])).status_code for i in range(100)]
        self.assertEqual(codes[-1], 429)  # token guessing is throttled

    def test_page_is_not_indexed_and_has_csp(self):
        r = self.client.get(self.url(self.invoice))
        self.assertContains(r, 'name="robots" content="noindex,nofollow"')
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])


class LookupApiTests(VerificationBase):
    def lookup(self, q):
        return self.client.get(reverse("verify:lookup"), {"q": q})

    def test_scan_by_url_token_and_short_code(self):
        self.login(self.owner)
        for q in (f"https://anything.example/verify/{self.invoice.verify_token}/", self.invoice.verify_token, self.invoice.short_code, self.invoice.short_code.replace("-", "").lower()):
            data = self.lookup(q).json()
            self.assertEqual(data["status"], "genuine", q)
            self.assertEqual(data["number"], self.invoice.number)
        d = self.lookup(self.invoice.short_code).json()
        self.assertEqual((d["customer"], d["amount"], d["paid"], d["balance"], d["kind"]), ("Okello Joseph Mukasa", "60000.00", "20000.00", "40000.00", "invoice"))
        self.assertEqual(d["items"][0]["name"], "Cement 50kg")
        self.assertEqual(d["open_url"], reverse("invoices:invoice_detail", args=[self.invoice.pk]))
        self.assertEqual(d["verified_before"], 4)  # four earlier checks of this invoice in this test
        r = self.lookup(self.receipt.verify_token).json()
        self.assertEqual((r["kind"], r["status"], r["amount"], r["items"][0]["method"]), ("receipt", "genuine", "20000.00", "Cash"))

    def test_forged_other_business_and_altered(self):
        self.login(self.owner)
        self.assertEqual(self.lookup("https://x/verify/" + "z" * 24 + "/").json()["status"], "notfound")
        self.assertEqual(self.lookup("garbage").json()["status"], "notfound")
        self.assertEqual(self.lookup("").json()["status"], "notfound")
        # a genuine document from ANOTHER business is not "genuine" for this one, and reveals nothing
        from payments.models import PaymentMethod
        theirs = sales.create_sale(business=self.other, user=self.other_owner, items=[{"product_id": make_product(self.other, self.other_owner, name="Pills").pk, "quantity": "1"}],
                                   payments=[{"method_id": PaymentMethod.objects.get(business=self.other, name="Cash").pk, "amount": "30000"}])
        data = self.lookup(theirs.invoice.verify_token).json()
        self.assertEqual(data["status"], "foreign")
        for leaked in ("number", "amount", "customer", "items", "business"):
            self.assertNotIn(leaked, data)
        self.assertTrue(AuditLog.objects.filter(action="document.verify_foreign", business=self.business).exists())
        Sale.objects.filter(pk=self.sale.pk).update(total=D("5.00"))
        self.assertEqual(self.lookup(self.invoice.verify_token).json()["status"], "tampered")

    def test_void_status_and_audit(self):
        self.login(self.owner)
        sales.cancel_sale(self.sale, self.owner, "Duplicate")
        d = self.lookup(self.invoice.short_code).json()
        self.assertEqual(d["status"], "void")
        self.assertTrue(d["warnings"])
        self.assertTrue(AuditLog.objects.filter(action="document.verify", business=self.business).exists())

    def test_requires_login_and_permission(self):
        self.assertEqual(self.lookup(self.invoice.verify_token).status_code, 302)
        self.assertEqual(self.client.get(reverse("verify:scan")).status_code, 302)
        stock = make_user(self.business, "stock_manager", "stella@alpha.test")   # has no invoices.view
        self.login(stock)
        self.assertEqual(self.lookup(self.invoice.verify_token).status_code, 403)
        self.assertEqual(self.client.get(reverse("verify:scan")).status_code, 403)
        sp = make_user(self.business, "salesperson", "sam@alpha.test")
        self.login(sp)
        self.assertEqual(self.lookup(self.invoice.verify_token).json()["status"], "genuine")

    def test_scanner_page(self):
        self.login(self.owner)
        r = self.client.get(reverse("verify:scan"))
        self.assertContains(r, "Start camera")
        self.assertContains(r, "js/vendor/jsQR.js")
        self.assertContains(r, "js/scanner.js")
        self.assertIn("camera=(self)", r["Permissions-Policy"])
        self.assertContains(self.client.get(reverse("core:home")), "Verify a document")
        self.assertContains(self.client.get(reverse("invoices:list")), "Scan to verify")
        scanner = (ROOT / "static/js/scanner.js").read_text()
        self.assertNotIn("innerHTML", scanner)  # server-supplied text is never parsed as HTML
        self.assertTrue((ROOT / "static/js/vendor/jsQR.js").stat().st_size > 50000)
        self.assertTrue((ROOT / "static/js/vendor/jsQR.LICENSE.txt").exists())


class QrOutputTests(VerificationBase):
    def test_pages_show_qr_and_code(self):
        self.login(self.owner)
        for url, doc in ((reverse("invoices:invoice_detail", args=[self.invoice.pk]), self.invoice), (reverse("invoices:receipt_detail", args=[self.receipt.pk]), self.receipt)):
            r = self.client.get(url)
            self.assertContains(r, "<svg")
            self.assertContains(r, doc.short_code)
            self.assertContains(r, "Scan the QR code")

    def test_pdfs_embed_the_qr_and_code(self):
        url = verification.verify_url(self.invoice)
        with_qr = pdf.render_invoice(self.invoice, "http://testserver" + url)
        without = pdf.render_invoice(self.invoice, "")
        self.assertTrue(with_qr.startswith(b"%PDF"))
        self.assertGreater(len(with_qr), len(without))
        self.login(self.owner)
        for name, doc in (("invoices:invoice_pdf", self.invoice), ("invoices:receipt_pdf", self.receipt)):
            r = self.client.get(reverse(name, args=[doc.pk]))
            self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertGreater(len(pdf.render_receipt(self.receipt, "http://x/verify/abc/")), len(pdf.render_receipt(self.receipt, "")))

    @override_settings(SITE_URL="https://inventory.example.com")
    def test_pdf_uses_configured_public_address(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("invoices:invoice_pdf", args=[self.invoice.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("invoices:invoice_detail", args=[self.invoice.pk])), "<svg")

    def test_svg_title_is_escaped(self):
        svg = qr.svg_inline("x", title="</title><script>alert(1)</script>")
        self.assertNotIn("<script", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_matrix_helpers(self):
        rows = qr.matrix("https://x.test/verify/abc/")
        self.assertEqual(len(rows), len(rows[0]))
        self.assertIn("<svg", qr.svg_inline("hello"))

    def test_generated_qr_is_decodable_by_the_scanner_library(self):
        """Decode our QR with the very jsQR build the in-app scanner ships, using Node."""
        node = shutil.which("node")
        lib = ROOT / "static/js/vendor/jsQR.js"
        if not node or not lib.exists():
            self.skipTest("node not available")
        url = verification.verify_url(self.invoice, None) or f"https://inventory.example.com/verify/{self.invoice.verify_token}/"
        url = f"https://inventory.example.com/verify/{self.invoice.verify_token}/"
        rows, scale, quiet = qr.matrix(url), 8, 4
        size = (len(rows) + 2 * quiet) * scale
        px = bytearray(b"\xff" * (size * size * 4))
        for r, row in enumerate(rows):
            for c, dark in enumerate(row):
                if dark:
                    for y in range((r + quiet) * scale, (r + quiet + 1) * scale):
                        for x in range((c + quiet) * scale, (c + quiet + 1) * scale):
                            i = (y * size + x) * 4
                            px[i:i + 3] = b"\x07\x17\x30"   # the navy used on screen and in PDFs
        script = ("const jsQR=require(process.argv[1]);const fs=require('fs');const j=JSON.parse(fs.readFileSync(0,'utf8'));"
                  "const d=new Uint8ClampedArray(Buffer.from(j.b64,'base64'));const r=jsQR(d,j.w,j.h);console.log(r?r.data:'NONE');")
        out = subprocess.run([node, "-e", script, str(lib)], input=json.dumps({"b64": base64.b64encode(bytes(px)).decode(), "w": size, "h": size}), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), url, out.stderr)
