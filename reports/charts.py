"""Chart data for the dashboard, summary and daily pages (server-rendered SVG / CSS, no JavaScript, CSP-safe).

Colour semantics are fixed across the product so a colour always means the same thing:
  blue   = sales / income            orange = expenses (money out you control)
  green  = healthy / paid / in stock amber  = low / due soon / needs attention
  red    = out of stock / overdue    slate  = neutral / cost / other
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, ExtractHour, TruncDate
from django.utils import timezone

ZERO = Decimal("0")

SALES, EXPENSE, HEALTHY, WARNING, DANGER, NEUTRAL = "#1c4a80", "#e8731a", "#059669", "#f59e0b", "#dc2626", "#94a3b8"
CATEGORICAL = ["#1c4a80", "#0f766e", "#6366f1", "#0ea5e9", "#64748b", "#a16207"]   # payment methods etc.
AGING = {"current": "#7fa9d6", "d30": WARNING, "d60": "#ea580c", "d60p": DANGER}
LEGEND = [("Sales / income", SALES), ("Expenses", EXPENSE), ("Healthy / paid", HEALTHY), ("Low / due soon", WARNING), ("Out / overdue", DANGER)]

MONEY = DecimalField(max_digits=20, decimal_places=2)


def compact(value):
    value = float(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{value:.0f}"


def nice_max(value):
    value = float(value)
    if value <= 0:
        return 1.0
    import math

    exp = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 2.5, 5, 10):
        if value <= m * exp:
            return m * exp
    return 10 * exp


def grouped_bars(labels, series, width=640, height=230, highlight_last=True, money=True):
    """series: [(name, color, [values])]. Returns geometry for a grouped bar chart."""
    left, right, top, bottom = 46, 8, 12, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = nice_max(max([float(v) for _, _, vals in series for v in vals] + [0]))
    n, k = len(labels), len(series)
    slot = plot_w / max(n, 1)
    bar_w = max(3.0, min(22.0, slot * 0.8 / k))
    bars = []
    for i, label in enumerate(labels):
        group_w = bar_w * k + (k - 1) * 2
        x0 = left + slot * i + (slot - group_w) / 2
        for j, (name, color, vals) in enumerate(series):
            v = float(vals[i])
            h = 0 if v <= 0 else max(2.0, v / peak * plot_h)
            bars.append({"x": round(x0 + j * (bar_w + 2), 1), "y": round(top + plot_h - h, 1), "w": round(bar_w, 1), "h": round(h, 1),
                         "color": color, "title": f"{label}: {name} {compact(v) if money else int(v)}"})
    ticks = [{"y": round(top + plot_h - plot_h * t, 1), "label": compact(peak * t) if money else str(int(peak * t))} for t in (0, 0.25, 0.5, 0.75, 1)]
    step = 1 if n <= 8 else 2
    xlabels = [{"x": round(left + slot * i + slot / 2, 1), "label": lab, "strong": highlight_last and i == n - 1} for i, lab in enumerate(labels) if (n - 1 - i) % step == 0]
    return {"width": width, "height": height, "left": left, "right_x": width - right, "baseline": top + plot_h, "bars": bars, "ticks": ticks, "xlabels": xlabels,
            "legend": [{"name": s[0], "color": s[1]} for s in series], "has_data": any(float(v) > 0 for _, _, vals in series for v in vals)}


def donut(items, palette=CATEGORICAL):
    """items: [(label, amount)] -> segments for a stroke-dasharray donut (circumference 100)."""
    items = [(l, Decimal(a)) for l, a in items if a and Decimal(a) > 0]
    total = sum((a for _, a in items), ZERO)
    segments, offset = [], 0.0
    for i, (label, amount) in enumerate(sorted(items, key=lambda t: t[1], reverse=True)):
        pct = float(amount / total * 100)
        segments.append({"label": label, "amount": amount, "pct": round(pct, 1), "dash": round(pct, 2), "gap": round(100 - pct, 2),
                         "offset": round(25 - offset, 2), "color": palette[i % len(palette)]})
        offset += pct
    return {"segments": segments, "total": total, "has_data": total > 0}


def hbars(items, color=SALES):
    """items: [(label, value, sublabel)] -> rows with a width percentage."""
    peak = max([float(v) for _, v, _ in items] + [0]) or 1
    return [{"label": l, "value": v, "sub": s, "pct": max(2, round(float(v) / peak * 100)) if float(v) > 0 else 0, "color": color} for l, v, s in items]


def stacked(parts):
    """parts: [(label, count, color, url)] -> segments with percentages."""
    total = sum(c for _, c, _, _ in parts) or 1
    return {"total": sum(c for _, c, _, _ in parts), "parts": [{"label": l, "count": c, "pct": round(c / total * 100, 1), "color": col, "url": u} for l, c, col, u in parts]}


# ------------------------------------------------------------------ data builders
def sales_vs_expenses(business, days=14, include_expenses=True):
    from expenses.models import Expense
    from sales.models import Sale

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    sales = dict(Sale.objects.filter(business=business, status="completed", sale_date__date__gte=start).annotate(d=TruncDate("sale_date"))
                 .values("d").annotate(t=Coalesce(Sum(F("subtotal") - F("discount_total")), Value(ZERO), output_field=MONEY)).values_list("d", "t"))
    series = [("Sales", SALES, [sales.get(start + timedelta(days=i), ZERO) for i in range(days)])]
    if include_expenses:
        exp = dict(Expense.objects.filter(business=business, status="posted", expense_date__gte=start).values("expense_date")
                   .annotate(t=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY)).values_list("expense_date", "t"))
        series.append(("Expenses", EXPENSE, [exp.get(start + timedelta(days=i), ZERO) for i in range(days)]))
    labels = [f"{(start + timedelta(days=i)):%d %b}" for i in range(days)]
    chart = grouped_bars(labels, series)
    chart["totals"] = [{"name": n, "color": c, "total": sum(v, ZERO)} for n, c, v in series]
    return chart


def payment_mix(business, days=30):
    from payments.models import Payment

    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (Payment.objects.filter(business=business, status="posted", direction="in", paid_on__gte=start)
            .values("method__name").annotate(t=Sum("amount")))
    return donut([(r["method__name"], r["t"]) for r in rows])


def top_products(business, days=30, limit=5):
    from sales.models import SaleItem

    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (SaleItem.objects.filter(sale__business=business, sale__status="completed", sale__sale_date__date__gte=start)
            .values("product_id", "description").annotate(net=Sum(ExpressionWrapper(F("quantity") * F("unit_price") - F("discount"), output_field=MONEY)), qty=Sum("quantity"))
            .order_by("-net")[:limit])
    return hbars([(r["description"], r["net"], f"{r['qty'].normalize():f} sold") for r in rows], SALES)


def stock_health(business):
    from inventory.models import StockLevel

    levels = StockLevel.objects.filter(business=business, product__is_active=True)
    out = levels.filter(quantity__lte=0).count()
    low = levels.filter(quantity__gt=0, quantity__lte=F("product__reorder_level")).count()
    total = levels.count()
    return stacked([("In stock", total - out - low, HEALTHY, "/inventory/"), ("Low", low, WARNING, "/inventory/?status=low"), ("Out of stock", out, DANGER, "/inventory/?status=out")])


def receivable_aging(business):
    from sales.models import Sale

    today = timezone.localdate()
    open_sales = Sale.objects.filter(business=business, status="completed", balance_due__gt=0)
    def total(q):
        return open_sales.filter(q).aggregate(t=Coalesce(Sum("balance_due"), Value(ZERO), output_field=MONEY))["t"]
    buckets = [("Not yet due", total(Q(due_date__gte=today) | Q(due_date__isnull=True)), AGING["current"]),
               ("1–30 days late", total(Q(due_date__lt=today, due_date__gte=today - timedelta(days=30))), AGING["d30"]),
               ("31–60 days late", total(Q(due_date__lt=today - timedelta(days=30), due_date__gte=today - timedelta(days=60))), AGING["d60"]),
               ("Over 60 days late", total(Q(due_date__lt=today - timedelta(days=60))), AGING["d60p"])]
    grand = sum((b[1] for b in buckets), ZERO)
    peak = max([b[1] for b in buckets] + [ZERO]) or Decimal(1)
    return {"rows": [{"label": l, "value": v, "color": c, "pct": max(2, round(float(v / peak * 100))) if v > 0 else 0} for l, v, c in buckets], "total": grand, "has_data": grand > 0}


def hourly_sales(business, day):
    from sales.models import Sale

    rows = (Sale.objects.filter(business=business, status="completed", sale_date__date=day).annotate(h=ExtractHour("sale_date", tzinfo=timezone.get_current_timezone()))
            .values("h").annotate(n=Count("id"), t=Coalesce(Sum(F("subtotal") - F("discount_total")), Value(ZERO), output_field=MONEY)))
    by_hour = {r["h"]: r["t"] for r in rows}
    hours = list(range(6, 23))
    return grouped_bars([f"{h:02d}" for h in hours], [("Sales", SALES, [by_hour.get(h, ZERO) for h in hours])], highlight_last=False)


def dashboard_charts(business, include_finance):
    charts = {"trend": sales_vs_expenses(business, 14, include_finance), "top": top_products(business), "stock": stock_health(business)}
    if include_finance:
        charts["mix"] = payment_mix(business)
        charts["aging"] = receivable_aging(business)
    return charts
