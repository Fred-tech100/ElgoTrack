"""Document authenticity: unguessable tokens, short codes and a tamper-evident signature.

Every invoice and receipt carries a QR code that points to ``/verify/<token>/``. The page that opens always shows what the
*database* says (number, date, total, status), so an altered or invented paper copy cannot match. The stored HMAC signature
additionally detects someone editing the issued figures in the database after the fact.
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime

from django.conf import settings

SHORT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def new_token():
    return secrets.token_urlsafe(18)  # 24 chars, 144 bits


def new_short_code():
    raw = "".join(secrets.choice(SHORT_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalise_short_code(text):
    raw = "".join(ch for ch in text.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}" if len(raw) == 12 else ""


def sign_values(kind, business_id, number, stamp, amount, party_id, token):
    key = hashlib.sha256(f"document-signature:{settings.SECRET_KEY}".encode()).digest()
    message = f"{kind}|{business_id}|{number}|{stamp}|{amount:.2f}|{party_id or 0}|{token}"
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def _facts(doc):
    label = doc._meta.label_lower
    if label == "invoices.invoice":
        return ("invoice", doc.business_id, doc.number, doc.issue_date.isoformat(), doc.sale.total, doc.sale.customer_id, doc.verify_token)
    if label == "businesses.platforminvoice":
        return ("platform_invoice", {
            "number": doc.number,
            "client": [doc.client_name, doc.client_email, doc.client_phone, doc.client_address],
            "issue_date": doc.issue_date.isoformat(),
            "due_date": doc.due_date.isoformat() if doc.due_date else "",
            "status": doc.status,
            "currency": [doc.currency_code, doc.currency_decimals],
            "amounts": [str(doc.subtotal), str(doc.tax_total), str(doc.total)],
            "notes": doc.notes,
            "terms": doc.terms,
            "short_code": doc.short_code,
            "token": doc.verify_token,
        })
    if label == "businesses.platformpayment":
        return ("platform_receipt", {
            "number": doc.number,
            "invoice": doc.invoice.number,
            "client": doc.invoice.client_name,
            "paid_on": doc.paid_on.isoformat(),
            "amount": str(doc.amount),
            "balance_after": str(doc.balance_after),
            "method": doc.method,
            "reference": doc.reference,
            "notes": doc.notes,
            "currency": [doc.invoice.currency_code, doc.invoice.currency_decimals],
            "short_code": doc.short_code,
            "token": doc.verify_token,
        })
    from datetime import timezone as dt_timezone

    return ("receipt", doc.business_id, doc.number, doc.issued_at.astimezone(dt_timezone.utc).strftime("%Y%m%d%H%M%S"), doc.amount, doc.customer_id, doc.verify_token)


def compute_signature(doc):
    facts = _facts(doc)
    if facts[0].startswith("platform_"):
        key = hashlib.sha256(f"document-signature:{settings.SECRET_KEY}".encode()).digest()
        message = json.dumps(facts, separators=(",", ":"), sort_keys=True)
        return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return sign_values(*facts)


def integrity_ok(doc):
    return bool(doc.signature) and hmac.compare_digest(doc.signature, compute_signature(doc))


def verify_url(doc, request=None):
    base = getattr(settings, "SITE_URL", "") or (request.build_absolute_uri("/").rstrip("/") if request is not None else "")
    return f"{base}/verify/{doc.verify_token}/"


def extract_query(text):
    """Accept a scanned URL, a raw token or a typed short code. Returns (token, short_code)."""
    text = (text or "").strip()
    if "/verify/" in text:
        tail = text.split("/verify/", 1)[1]
        token = tail.split("/", 1)[0].split("?", 1)[0]
        return token, ""
    code = normalise_short_code(text)
    return ("", code) if code else (text[:40], "")


def find_document(token="", short_code=""):
    from businesses.models import PlatformInvoice, PlatformPayment
    from .models import Invoice, Receipt

    for model in (Invoice, Receipt):
        qs = model.objects.select_related("business")
        doc = qs.filter(verify_token=token).first() if token else (qs.filter(short_code=short_code).first() if short_code else None)
        if doc is not None:
            return doc
    for model in (PlatformInvoice, PlatformPayment):
        qs = model.objects.select_related("invoice") if model is PlatformPayment else model.objects.all()
        doc = qs.filter(verify_token=token).first() if token else (
            qs.filter(short_code=short_code).first() if short_code else None
        )
        if doc is not None:
            return doc
    return None


def describe(doc, detailed=False):
    """Facts about a document as the database holds them. ``detailed`` is only for the issuing business."""
    if doc._meta.label_lower in {"businesses.platforminvoice", "businesses.platformpayment"}:
        return _describe_platform_document(doc, detailed)

    from .models import Invoice

    is_invoice = isinstance(doc, Invoice)
    warnings, status = [], "genuine"
    if not integrity_ok(doc):
        status = "tampered"
    if is_invoice:
        sale = doc.sale
        amount, date_text, customer = sale.total, doc.issue_date, sale.customer
        if doc.status == "void" or sale.status == "cancelled":
            status = "void" if status == "genuine" else status
            warnings.append("This invoice was cancelled and is no longer valid.")
        if sale.returned_total:
            warnings.append("Goods on this invoice were partly or fully returned.")
        extra = {"paid": sale.amount_paid, "balance": sale.balance_due, "payment_status": sale.get_payment_status_display(), "sale_number": sale.number}
    else:
        amount, date_text, customer = doc.amount, doc.issued_at, doc.customer
        reversed_n = doc.payments.filter(status="reversed").count()
        if reversed_n:
            status = "void" if status == "genuine" else status
            warnings.append(f"{reversed_n} payment{'s' if reversed_n != 1 else ''} on this receipt {'were' if reversed_n != 1 else 'was'} reversed.")
        extra = {}
    data = {
        "kind": "invoice" if is_invoice else "receipt", "number": doc.number, "business": doc.business.name, "date": date_text,
        "date_has_time": isinstance(date_text, datetime),
        "amount": amount, "currency": doc.business.currency_code, "decimals": doc.business.currency_decimals, "status": status, "warnings": warnings, "short_code": doc.short_code,
        "customer_hint": _mask(customer.name) if customer else "Walk-in customer",
    }
    if detailed:
        data.update(extra)
        data["customer"] = customer.name if customer else "Walk-in customer"
        data["verified_before"] = doc.verify_count
        if is_invoice:
            data["items"] = [{"name": i.description, "qty": i.quantity, "total": i.line_total} for i in doc.sale.items.all()]
        else:
            data["items"] = [{"name": p.sale.number if p.sale else "Payment on account", "qty": None, "total": p.amount, "method": p.method.name}
                             for p in doc.payments.select_related("method", "sale")]
    return data


def _describe_platform_document(doc, detailed=False):
    is_invoice = doc._meta.label_lower == "businesses.platforminvoice"
    warnings, status = [], "genuine"
    if not integrity_ok(doc):
        status = "tampered"
    if is_invoice:
        amount, date_text, kind = doc.total, doc.issue_date, "invoice"
        client, number = doc.client_name, doc.number
        payment_status = doc.payment_status
        if doc.status == "void":
            status = "void" if status == "genuine" else status
            warnings.append("This invoice was voided and is no longer valid.")
        extra = {
            "paid": doc.amount_paid,
            "balance": doc.balance_due,
            "payment_status": payment_status.capitalize(),
            "items": [{"name": item.description, "qty": item.quantity, "total": item.line_total}
                      for item in doc.items.all()],
        }
    else:
        invoice = doc.invoice
        amount, date_text, kind = doc.amount, doc.paid_on, "receipt"
        client, number = invoice.client_name, doc.number
        payment_status = "Paid" if doc.balance_after == 0 else "Partially paid"
        if invoice.status == "void":
            warnings.append("The related invoice was voided after this receipt was issued.")
        extra = {
            "paid": doc.total_paid_after,
            "balance": doc.balance_after,
            "payment_status": payment_status,
            "invoice_number": invoice.number,
            "items": [{"name": item.description, "qty": item.quantity, "total": item.line_total}
                      for item in invoice.items.all()],
        }
    data = {
        "kind": kind, "number": number, "business": "ElgonNova Technologies", "date": date_text,
        "date_has_time": isinstance(date_text, datetime),
        "amount": amount, "currency": doc.currency_code if is_invoice else doc.invoice.currency_code,
        "decimals": doc.currency_decimals if is_invoice else doc.invoice.currency_decimals,
        "status": status, "warnings": warnings, "short_code": doc.short_code,
        "customer_hint": _mask(client), "customer": client,
        "payment_status": payment_status,
    }
    if detailed:
        data.update(extra)
        data["verified_before"] = doc.verify_count
    return data


def _mask(name):
    parts = name.split()
    return parts[0] + (f" {parts[1][0]}." if len(parts) > 1 else "")
