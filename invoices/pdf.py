"""PDF rendering for invoices, receipts and customer statements (ReportLab)."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from django.contrib.staticfiles import finders
from django.utils.html import escape

from core import qr

NAVY = colors.HexColor("#0b2a4a")
ORANGE = colors.HexColor("#e8731a")
GREY = colors.HexColor("#64748b")
LIGHT = colors.HexColor("#eef2f7")

styles = getSampleStyleSheet()
H = ParagraphStyle("h", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, textColor=NAVY, alignment=0, spaceAfter=2)
SMALL = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, textColor=GREY, leading=11)
BODY = ParagraphStyle("b", parent=styles["Normal"], fontSize=9.5, leading=12)
BOLD = ParagraphStyle("bb", parent=BODY, fontName="Helvetica-Bold")


class QRCode(Flowable):
    """Vector QR code (crisp at any print size) with a 2-module quiet zone."""

    BORDER = 2

    def __init__(self, data, size=28 * mm):
        super().__init__()
        self.rows = qr.matrix(data)
        self.width = self.height = size

    def draw(self):
        n = len(self.rows) + 2 * self.BORDER
        unit = self.width / n
        self.canv.setFillColor(colors.white)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        self.canv.setFillColor(colors.HexColor("#071730"))
        for r, row in enumerate(self.rows):
            for c, dark in enumerate(row):
                if dark:
                    self.canv.rect((c + self.BORDER) * unit, self.height - (r + self.BORDER + 1) * unit, unit + 0.1, unit + 0.1, stroke=0, fill=1)


def verification_block(doc, url, kind, width=180 * mm):
    """QR + short code printed on every invoice and receipt so the owner can confirm it is genuine."""
    if not url:
        return []
    text = Paragraph(
        f"<b>Verify this {kind}</b><br/>Scan the QR code with a phone camera. The page that opens shows the number, date and "
        f"total recorded by the issuing business: they must match this paper copy.<br/>"
        f"<b>Code: {doc.short_code}</b><br/><font size='7' color='#64748b'>{url}</font>", SMALL)
    qr_size = 34 * mm
    table = Table([[QRCode(url, qr_size), text]], colWidths=[qr_size + 4 * mm, width - qr_size - 4 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("LINEABOVE", (0, 0), (-1, 0), 0.4, colors.HexColor("#cbd5e1")), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    return [Spacer(1, 6 * mm), table]


def money(business, value):
    return f"{business.currency_code} {value:,.{business.currency_decimals}f}"


def qty(value):
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _logo(business, height=14 * mm):
    if business.logo:
        try:
            img = Image(business.logo.path)
            ratio = img.imageWidth / img.imageHeight
            img.drawHeight, img.drawWidth = height, height * ratio
            img.hAlign = "LEFT"
            return img
        except Exception:
            return None
    return None


def _letterhead(business):
    parts = [Paragraph(business.name, H)]
    lines = [x for x in [business.address.replace("\n", ", "), business.phone, business.email] if x]
    if lines:
        parts.append(Paragraph(" · ".join(lines), SMALL))
    logo = _logo(business)
    return ([logo, Spacer(1, 3 * mm)] if logo else []) + parts


def _table_style(header=True):
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    return TableStyle(style)


def _build(story, pagesize, title):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=title)
    doc.build(story)
    return buf.getvalue()


def render_invoice(invoice, verify_url=""):
    sale, b = invoice.sale, invoice.business
    story = _letterhead(b)
    story.append(Spacer(1, 4 * mm))
    meta = [
        [Paragraph("INVOICE" + (" (VOID)" if invoice.status == "void" else ""), H), ""],
        [Paragraph(f"<b>Invoice no:</b> {invoice.number}<br/><b>Date:</b> {invoice.issue_date:%d %b %Y}"
                   + (f"<br/><b>Due:</b> {invoice.due_date:%d %b %Y}" if invoice.due_date else ""), BODY),
         Paragraph("<b>Bill to</b><br/>" + (f"{sale.customer.name}<br/>{sale.customer.phone}<br/>{sale.customer.address}"
                                           if sale.customer else "Walk-in customer"), BODY)],
    ]
    t = Table(meta, colWidths=[90 * mm, 90 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (0, 0), (1, 0))]))
    story += [t, Spacer(1, 5 * mm)]
    rows = [["Item", "Qty", "Unit price", "Discount", "Tax", "Amount"]]
    for i in sale.items.all():
        rows.append([Paragraph(i.description, BODY), qty(i.quantity), money(b, i.unit_price), money(b, i.discount),
                     money(b, i.tax_amount), money(b, i.line_total)])
    items = Table(rows, colWidths=[62 * mm, 16 * mm, 28 * mm, 24 * mm, 22 * mm, 28 * mm], repeatRows=1)
    st = _table_style()
    st.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
    items.setStyle(st)
    story += [items, Spacer(1, 4 * mm)]
    totals = [["Subtotal", money(b, sale.subtotal)], ["Discount", f"- {money(b, sale.discount_total)}"],
              [b.tax_name or "Tax", money(b, sale.tax_total)], ["Total", money(b, sale.total)]]
    if sale.returned_total:
        totals.append(["Returns", f"- {money(b, sale.returned_total)}"])
    totals += [["Amount paid", money(b, sale.amount_paid)], ["Balance due", money(b, sale.balance_due)]]
    tt = Table(totals, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                            ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                            ("TEXTCOLOR", (0, -1), (-1, -1), ORANGE), ("LINEABOVE", (0, 3), (-1, 3), 0.6, NAVY)]))
    story += [tt, Spacer(1, 3 * mm),
              Paragraph(f"<b>Payment status:</b> {sale.get_payment_status_display()}", BODY)]
    if sale.notes:
        story.append(Paragraph(f"<b>Notes:</b> {sale.notes}", BODY))
    if invoice.terms:
        story += [Spacer(1, 4 * mm), Paragraph("<b>Terms</b>", BODY), Paragraph(invoice.terms.replace("\n", "<br/>"), SMALL)]
    story += verification_block(invoice, verify_url, "invoice")
    return _build(story, A4, invoice.number)


def render_receipt(receipt, verify_url=""):
    b = receipt.business
    story = _letterhead(b) + [Spacer(1, 3 * mm), Paragraph("RECEIPT", H)]
    story.append(Paragraph(
        f"<b>Receipt no:</b> {receipt.number}<br/><b>Date:</b> {receipt.issued_at:%d %b %Y %H:%M}<br/>"
        f"<b>Customer:</b> {receipt.customer.name if receipt.customer else 'Walk-in customer'}", BODY))
    story.append(Spacer(1, 4 * mm))
    rows = [["Applied to", "Method", "Amount"]]
    for p in receipt.payments.select_related("method", "sale"):
        rows.append([p.sale.number if p.sale else "Payment on account", p.method.name, money(b, p.amount)])
    rows.append(["Total received", "", money(b, receipt.amount)])
    t = Table(rows, colWidths=[55 * mm, 35 * mm, 35 * mm])
    st = _table_style()
    st.add("ALIGN", (2, 0), (2, -1), "RIGHT")
    st.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    t.setStyle(st)
    story.append(t)
    sale_ids = {p.sale for p in receipt.payments.select_related("sale") if p.sale}
    for s in sale_ids:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(f"Balance on {s.number}: <b>{money(b, s.balance_due)}</b>", BODY))
    if receipt.customer:
        story.append(Paragraph(f"Account balance: <b>{money(b, receipt.customer.balance)}</b>", BODY))
    story += [Spacer(1, 4 * mm), Paragraph("Thank you for your business.", SMALL)]
    story += verification_block(receipt, verify_url, "receipt", width=118 * mm)
    return _build(story, A5, receipt.number)


def render_statement(business, party, kind, start, end, statement):
    story = _letterhead(business) + [Spacer(1, 3 * mm), Paragraph(f"{kind.title()} statement", H)]
    story.append(Paragraph(f"<b>{party.name}</b> ({party.code})<br/>Period: {start:%d %b %Y} – {end:%d %b %Y}", BODY))
    story.append(Spacer(1, 4 * mm))
    rows = [["Date", "Description", "Amount", "Balance"],
            ["", "Previous balance", "", money(business, statement["opening"])]]
    for e, running in statement["rows"]:
        rows.append([f"{e.entry_date:%d %b %Y}", Paragraph(e.description, BODY), money(business, e.amount), money(business, running)])
    rows.append(["", "Current balance", "", money(business, statement["closing"])])
    t = Table(rows, colWidths=[28 * mm, 80 * mm, 35 * mm, 35 * mm], repeatRows=1)
    st = _table_style()
    st.add("ALIGN", (2, 0), (-1, -1), "RIGHT")
    st.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    t.setStyle(st)
    story.append(t)
    return _build(story, A4, f"{kind} statement")


def _platform_money(invoice, value):
    return f"{invoice.currency_code} {value:,.{invoice.currency_decimals}f}"


def _platform_letterhead(width=180 * mm):
    logo_path = finders.find("images/landing/logo.png")
    brand = Table(
        [[Image(logo_path, width=14 * mm, height=14 * mm),
          Paragraph("ElgonNova Technologies", H)]],
        colWidths=[18 * mm, width - 18 * mm],
    )
    brand.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return [brand, Paragraph("Platform billing - Inventory, software and professional services", SMALL)]


def render_platform_invoice(invoice, verify_url=""):
    story = _platform_letterhead() + [Spacer(1, 5 * mm), Paragraph("INVOICE", H)]
    details = [
        [Paragraph(f"<b>Invoice no:</b> {escape(invoice.number)}<br/><b>Issue date:</b> {invoice.issue_date:%d %b %Y}"
                   + (f"<br/><b>Due date:</b> {invoice.due_date:%d %b %Y}" if invoice.due_date else ""), BODY),
         Paragraph("<b>Bill to</b><br/>" + "<br/>".join(
             escape(value) for value in (invoice.client_name, invoice.client_email, invoice.client_phone,
                                         invoice.client_address) if value
         ), BODY)],
    ]
    detail_table = Table(details, colWidths=[85 * mm, 95 * mm])
    detail_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [detail_table, Spacer(1, 5 * mm)]
    rows = [["Service or item", "Qty", "Unit price", "Tax", "Amount"]]
    for item in invoice.items.all():
        rows.append([
            Paragraph(escape(item.description), BODY), qty(item.quantity), _platform_money(invoice, item.unit_price),
            f"{item.tax_rate:.2f}%", _platform_money(invoice, item.line_total),
        ])
    items = Table(rows, colWidths=[68 * mm, 17 * mm, 32 * mm, 23 * mm, 40 * mm], repeatRows=1)
    style = _table_style()
    style.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
    items.setStyle(style)
    story += [items, Spacer(1, 4 * mm)]
    totals = Table([
        ["Subtotal", _platform_money(invoice, invoice.subtotal)],
        ["Tax", _platform_money(invoice, invoice.tax_total)],
        ["Total", _platform_money(invoice, invoice.total)],
        ["Paid", _platform_money(invoice, invoice.amount_paid)],
        ["Balance due", _platform_money(invoice, invoice.balance_due)],
    ], colWidths=[40 * mm, 45 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), ORANGE), ("LINEABOVE", (0, 2), (-1, 2), 0.6, NAVY),
    ]))
    story.append(totals)
    story += [Spacer(1, 3 * mm), Paragraph(
        f"<b>Invoice status:</b> {escape(invoice.get_status_display())}<br/>"
        f"<b>Payment status:</b> {escape(invoice.payment_status.capitalize())}", BODY,
    )]
    if invoice.notes:
        story += [Spacer(1, 5 * mm), Paragraph("<b>Notes</b>", BODY),
                  Paragraph(escape(invoice.notes).replace("\n", "<br/>"), SMALL)]
    if invoice.terms:
        story += [Spacer(1, 4 * mm), Paragraph("<b>Terms and conditions</b>", BODY),
                  Paragraph(escape(invoice.terms).replace("\n", "<br/>"), SMALL)]
    story += verification_block(invoice, verify_url, "invoice")
    return _build(story, A4, invoice.number)


def render_platform_receipt(receipt, verify_url=""):
    invoice = receipt.invoice
    story = _platform_letterhead(118 * mm) + [Spacer(1, 4 * mm), Paragraph("PAYMENT RECEIPT", H)]
    story.append(Paragraph(
        f"<b>Receipt no:</b> {escape(receipt.number)}<br/><b>Date:</b> {receipt.paid_on:%d %b %Y}<br/>"
        f"<b>Received from:</b> {escape(invoice.client_name)}<br/>"
        f"<b>Invoice:</b> {escape(invoice.number)}", BODY))
    story += [Spacer(1, 4 * mm), Paragraph("Services or items on this invoice", BOLD), Spacer(1, 2 * mm)]
    item_rows = [["Description", "Qty", "Line total"]]
    for item in invoice.items.all():
        item_rows.append([Paragraph(escape(item.description), BODY), qty(item.quantity), _platform_money(invoice, item.line_total)])
    item_table = Table(item_rows, colWidths=[68 * mm, 17 * mm, 33 * mm], repeatRows=1)
    item_style = _table_style()
    item_style.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
    item_table.setStyle(item_style)
    story += [item_table, Spacer(1, 4 * mm)]
    rows = [["Payment method", "Reference", "Amount received"],
            [escape(receipt.method), escape(receipt.reference) or "-", _platform_money(invoice, receipt.amount)]]
    table = Table(rows, colWidths=[39 * mm, 35 * mm, 41 * mm])
    style = _table_style()
    style.add("ALIGN", (2, 0), (2, -1), "RIGHT")
    table.setStyle(style)
    story += [table, Spacer(1, 4 * mm),
              Paragraph(f"<b>Payment status:</b> {'Paid' if receipt.balance_after == 0 else 'Partially paid'}", BODY),
              Paragraph(f"<b>Invoice total:</b> {_platform_money(invoice, invoice.total)}<br/>"
                        f"<b>Total paid:</b> {_platform_money(invoice, receipt.total_paid_after)}<br/>"
                        f"<b>Balance due:</b> {_platform_money(invoice, receipt.balance_after)}", BODY),
              Spacer(1, 5 * mm), Paragraph("Thank you for your payment.", SMALL)]
    if receipt.notes:
        story += [Spacer(1, 3 * mm), Paragraph(escape(receipt.notes).replace("\n", "<br/>"), SMALL)]
    story += verification_block(receipt, verify_url, "receipt", width=118 * mm)
    return _build(story, A5, receipt.number)
