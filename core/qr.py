"""QR code helpers (segno, pure Python): inline SVG for pages and a vector matrix for PDFs."""
import segno
from django.utils.safestring import mark_safe


def make(data, error="m"):
    return segno.make(data, error=error, micro=False)


def svg_inline(data, scale=4, border=2, dark="#0b2a4a", title="QR code"):
    svg = make(data).svg_inline(scale=scale, border=border, dark=dark, light="#ffffff", title=title, omitsize=False)
    # Safe: segno generates the markup from the module matrix and HTML-escapes the title; no caller data reaches it unescaped.
    return mark_safe(svg)  # nosec B703 B308


def matrix(data, error="m"):
    """Rows of booleans (True = dark module)."""
    return [[bool(c) for c in row] for row in make(data, error).matrix]
