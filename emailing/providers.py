import json
import logging
import urllib.error
import urllib.request
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


class EmailProviderError(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class BrevoProvider:
    endpoint = "https://api.brevo.com/v3/smtp/email"

    def send(self, *, subject, recipient, html_content, text_content, attachments):
        if not settings.BREVO_API_KEY:
            raise EmailProviderError("BREVO_API_KEY is not configured.")
        sender_name, sender_address = self._sender()
        body = {
            "sender": {"name": sender_name, "email": sender_address},
            "to": [{"email": recipient}],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content,
        }
        if settings.SUPPORT_EMAIL:
            body["replyTo"] = {"email": settings.SUPPORT_EMAIL, "name": f"{settings.DEFAULT_FROM_NAME} Support"}
        if attachments:
            body["attachment"] = attachments
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "accept": "application/json",
                "api-key": settings.BREVO_API_KEY,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            # The endpoint is a fixed HTTPS URL and is not influenced by request data.
            with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
                data = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise EmailProviderError(
                f"Brevo returned HTTP {exc.code}.",
                retryable=exc.code == 429 or exc.code >= 500,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmailProviderError("Could not connect to Brevo.", retryable=True) from exc
        return data.get("messageId", "")

    @staticmethod
    def _sender():
        from email.utils import parseaddr

        display_name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
        return display_name or settings.DEFAULT_FROM_NAME, address


class DjangoEmailProvider:
    """Safe development/test provider using Django's configured console/locmem backend."""

    def send(self, *, subject, recipient, html_content, text_content, attachments):
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=self._sender(),
            to=[recipient],
        )
        message.attach_alternative(html_content, "text/html")
        for attachment in attachments:
            import base64

            message.attach(
                attachment["name"],
                base64.b64decode(attachment["content"], validate=True),
                attachment.get("contentType", "application/octet-stream"),
            )
        try:
            sent = message.send(fail_silently=False)
        except Exception as exc:
            logger.warning("Django email provider failed: %s", type(exc).__name__)
            raise EmailProviderError("Django email backend could not send the message.", retryable=True) from exc
        if sent != 1:
            raise EmailProviderError("Django email backend did not accept the message.", retryable=True)
        return ""

    @staticmethod
    def _sender():
        display_name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
        return formataddr((display_name or settings.DEFAULT_FROM_NAME, address))


def get_provider():
    if settings.EMAIL_PROVIDER == "brevo":
        return BrevoProvider()
    if settings.EMAIL_PROVIDER == "django":
        return DjangoEmailProvider()
    raise EmailProviderError("EMAIL_PROVIDER must be 'brevo' or 'django'.")
