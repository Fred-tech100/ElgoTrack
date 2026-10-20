import json
import urllib.error
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from emailing.models import EmailDelivery
from emailing.providers import BrevoProvider, EmailProviderError
from emailing.services import queue_email, send_welcome
from notifications.models import Notification

from .helpers import TenantTestCase


class EmailDeliveryTests(TenantTestCase):
    @override_settings(EMAIL_PROVIDER="django", ELGOTRACK_PUBLIC_URL="https://app.example.test")
    def test_welcome_email_renders_and_is_logged(self):
        with self.captureOnCommitCallbacks(execute=True):
            delivery = send_welcome(self.owner)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.SENT)
        self.assertEqual(delivery.email_type, "welcome")
        self.assertEqual(mail.outbox[-1].to, [self.owner.email])
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn("Alpha Hardware", html)
        self.assertIn("https://app.example.test/accounts/login/", html)
        self.assertIn("ElgoTrack", html)

    @override_settings(EMAIL_PROVIDER="brevo", BREVO_API_KEY="")
    def test_missing_brevo_key_is_logged_without_raising(self):
        with self.captureOnCommitCallbacks(execute=True):
            delivery = queue_email(
                email_type="test",
                recipient=self.owner.email,
                subject="Test",
                template="welcome",
                context={"first_name": "Owner", "business_name": "Alpha"},
                user=self.owner,
                business=self.business,
            )

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.FAILED)
        self.assertIn("BREVO_API_KEY is not configured", delivery.error_message)

    @override_settings(BREVO_API_KEY="test-api-key", DEFAULT_FROM_EMAIL="no-reply@elgotrack.site")
    def test_brevo_api_request_keeps_key_in_header_and_records_provider_id(self):
        response = mock.MagicMock()
        response.read.return_value = b'{"messageId":"brevo-message-123"}'
        response.__enter__.return_value = response
        with mock.patch("emailing.providers.urllib.request.urlopen", return_value=response) as urlopen:
            message_id = BrevoProvider().send(
                subject="Test",
                recipient="person@example.test",
                html_content="<p>Hello</p>",
                text_content="Hello",
                attachments=[],
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Api-key"), "test-api-key")
        self.assertNotIn("test-api-key", json.loads(request.data))
        self.assertEqual(message_id, "brevo-message-123")

    @override_settings(BREVO_API_KEY="test-api-key")
    def test_brevo_auth_error_is_permanent_and_does_not_echo_response_body(self):
        response = urllib.error.HTTPError(
            BrevoProvider.endpoint,
            401,
            "Unauthorized",
            {},
            None,
        )
        with mock.patch("emailing.providers.urllib.request.urlopen", side_effect=response):
            with self.assertRaises(EmailProviderError) as error:
                BrevoProvider().send(
                    subject="Test",
                    recipient="person@example.test",
                    html_content="<p>Hello</p>",
                    text_content="Hello",
                    attachments=[],
                )
        self.assertFalse(error.exception.retryable)
        self.assertEqual(str(error.exception), "Brevo returned HTTP 401.")

    def test_missing_recipient_is_not_queued(self):
        self.assertIsNone(queue_email(
            email_type="test",
            recipient="",
            subject="Test",
            template="welcome",
            context={},
        ))
        self.assertFalse(EmailDelivery.objects.exists())

    @override_settings(EMAIL_PROVIDER="django", ELGOTRACK_PUBLIC_URL="https://app.example.test")
    def test_registration_sends_pending_business_welcome(self):
        from accounts.models import User

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("accounts:register"), {
                "name": "New Shop",
                "business_type": "retail",
                "owner_name": "Nina Owner",
                "owner_email": "nina@newshop.test",
                "owner_phone": "+256700000001",
                "password": "Another-Strong-Pass-2026",
                "password_confirm": "Another-Strong-Pass-2026",
            })

        self.assertEqual(response.status_code, 302)
        owner = User.objects.get(email="nina@newshop.test")
        delivery = EmailDelivery.objects.get(user=owner, email_type="welcome")
        self.assertEqual(delivery.status, EmailDelivery.SENT)
        self.assertIn("awaiting approval", mail.outbox[-1].body.lower())

    def test_queue_failure_is_logged_without_raising(self):
        with mock.patch("emailing.tasks.deliver_email.delay", side_effect=OSError):
            with self.captureOnCommitCallbacks(execute=True):
                delivery = queue_email(
                    email_type="test",
                    recipient=self.owner.email,
                    subject="Test",
                    template="welcome",
                    context={"first_name": "Owner", "business_name": "Alpha"},
                    business=self.business,
                    user=self.owner,
                )

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.FAILED)
        self.assertIn("Could not enqueue email", delivery.error_message)

    @override_settings(EMAIL_PROVIDER="django", ELGOTRACK_PUBLIC_URL="https://app.example.test")
    def test_password_reset_and_password_change_are_transactional_emails(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("accounts:password_reset"), {"email": self.owner.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[-1].to, [self.owner.email])
        self.assertIn("/accounts/reset/", mail.outbox[-1].body)
        self.assertTrue(EmailDelivery.objects.filter(email_type="password_reset", status=EmailDelivery.SENT).exists())

        self.login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("accounts:password_change"), {
                "old_password": "S3cure-pass-phrase!",
                "new_password1": "Another-Strong-Pass-2026",
                "new_password2": "Another-Strong-Pass-2026",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[-1].subject, "Your ElgoTrack password was changed")
        self.assertEqual(mail.outbox[-1].to, [self.owner.email])

    @override_settings(EMAIL_PROVIDER="django", NOTIFICATION_CHANNELS=["email"],
                       ELGOTRACK_PUBLIC_URL="https://app.example.test")
    def test_notification_email_targets_only_supplied_business_users(self):
        self.product.reorder_level = 101
        self.product.save(update_fields=["reorder_level"])
        with self.captureOnCommitCallbacks(execute=True):
            from notifications.services import sync_notifications

            sync_notifications(self.business)
            sync_notifications(self.business)

        self.assertEqual(EmailDelivery.objects.filter(email_type="low_stock").count(), 1)
        self.assertEqual(mail.outbox[-1].to, [self.owner.email])
        notification = Notification.objects.get(business=self.business, kind="low_stock")
        event = EmailDelivery.objects.get(related_id=str(notification.pk), email_type="low_stock")
        self.assertEqual(event.business_id, self.business.pk)
        self.assertEqual(event.user_id, self.owner.pk)
        self.assertNotIn(self.other_owner.email, mail.outbox[-1].to)

    @override_settings(EMAIL_PROVIDER="django", ELGOTRACK_PUBLIC_URL="https://app.example.test")
    def test_invoice_is_emailed_as_pdf_only_to_its_tenant_customer(self):
        from contacts.models import Customer
        from sales.services import create_sale

        customer = Customer.objects.create(
            business=self.business,
            name="A Customer",
            email="customer@alpha.test",
            customer_type=Customer.CASH,
        )
        sale = create_sale(
            business=self.business,
            user=self.owner,
            customer=customer,
            items=[{"product_id": self.product.pk, "quantity": "1"}],
            payments=[{"method_id": self.cash.pk, "amount": "30000"}],
        )
        invoice = sale.invoice
        self.login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("invoices:invoice_email", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[-1].to, [customer.email])
        self.assertEqual(mail.outbox[-1].attachments[0][0], f"{invoice.number}.pdf")
        self.assertEqual(mail.outbox[-1].attachments[0][2], "application/pdf")

        receipt = sale.payments.first().receipt
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("invoices:receipt_email", args=[receipt.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[-1].subject, f"Receipt {receipt.number} from {self.business.name}")
        self.assertEqual(mail.outbox[-1].attachments[0][0], f"{receipt.number}.pdf")

    @override_settings(EMAIL_PROVIDER="django")
    def test_customer_statement_email_includes_pdf_and_period(self):
        from contacts.models import Customer

        customer = Customer.objects.create(
            business=self.business,
            name="A Customer",
            email="customer@alpha.test",
        )
        self.login(self.owner)
        url = reverse("customers:statement_email", args=[customer.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{url}?start=2026-10-01&end=2026-10-20",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox[-1].to, [customer.email])
        self.assertIn("01 Oct 2026 to 20 Oct 2026", mail.outbox[-1].body)
        self.assertTrue(mail.outbox[-1].attachments[0][0].startswith("customer-statement-"))
