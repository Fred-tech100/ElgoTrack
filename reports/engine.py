"""Report engine: standard reports and the custom report builder share these primitives.

Every report is computed in the database (aggregations) and returned as a ``ReportResult`` that the
HTML view and the CSV / Excel / PDF exporters all render, so the numbers always agree.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When)
from django.db.models.functions import Coalesce, TruncDate, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from core.utils import parse_date

ZERO = Decimal("0")
MAX_DETAIL_ROWS = 5000
DEC = DecimalField(max_digits=20, decimal_places=3)
MONEY = DecimalField(max_digits=20, decimal_places=2)

TEXT, QTY, MONEY_KIND, DATE, INT = "text", "qty", "money", "date", "int"


@dataclass
class Column:
    key: str
    label: str
    kind: str = TEXT


@dataclass
class ReportResult:
    title: str
    columns: list
    rows: list
    totals: dict = field(default_factory=dict)
    subtitle: str = ""
    truncated: bool = False
    note: str = ""


@dataclass
class Filters:
    start: date
    end: date
    category: object = None
    product: object = None
    supplier: object = None
    customer: object = None
    user: object = None
    method: object = None
    movement_type: str = ""
    period: str = "day"
    group_by: str = ""

    @classmethod
    def from_request(cls, request, business, default_days=30):
        from accounts.models import User
        from contacts.models import Customer, Supplier
        from payments.models import PaymentMethod
        from products.models import Category, Product

        g = request.GET
        today = timezone.localdate()
        end = parse_date(g.get("end"), today)
        days = g.get("days")
        if days and days.isdigit() and not g.get("start"):
            start = end - timedelta(days=int(days))
        else:
            start = parse_date(g.get("start"), end - timedelta(days=default_days))
        if start > end:
            start, end = end, start

        def pick(model, key, **extra):
            val = g.get(key, "")
            return model.objects.filter(business=business, **extra).filter(pk=val).first() if val.isdigit() else None

        period = g.get("period", "day")
        return cls(
            start=start, end=end, category=pick(Category, "category"), product=pick(Product, "product"),
            supplier=pick(Supplier, "supplier"), customer=pick(Customer, "customer"),
            user=pick(User, "user"), method=pick(PaymentMethod, "method"),
            movement_type=g.get("movement_type", ""), period=period if period in ("day", "week", "month") else "day",
            group_by=g.get("group_by", ""),
        )

    def describe(self):
        parts = [f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"]
        for label, val in [("Category", self.category), ("Product", self.product), ("Supplier", self.supplier),
                           ("Customer", self.customer), ("User", self.user), ("Method", self.method)]:
            if val:
                parts.append(f"{label}: {val}")
        if self.movement_type:
            parts.append(f"Type: {self.movement_type.replace('_', ' ')}")
        return " · ".join(parts)


def _wrap(expr, field=DEC):
    return ExpressionWrapper(expr, output_field=field)


def _sum(expr, **kw):
    return Coalesce(Sum(expr, **kw), Value(ZERO), output_field=DEC)


def _period(field_name, period, is_datetime):
    if is_datetime and period == "day":
        return TruncDate(field_name)
    fn = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}[period]
    return fn(field_name)


def _fmt_period(value, period):
    if value is None:
        return ""
    if period == "month":
        return f"{value:%b %Y}"
    if period == "week":
        return f"Week of {value:%d %b %Y}"
    return f"{value:%d %b %Y}"


def _totals(rows, keys):
    return {k: sum((r.get(k) or ZERO for r in rows), ZERO) for k in keys}


# =============================================================== SALES (with returns)
def _sale_spec(group, side, period):
    """Annotations producing g_key / g_label for sale items (side='item') or return items ('ret')."""
    sale = "sale__" if side == "item" else "sale_item__sale__"
    p = "" if side == "item" else "sale_item__"
    datef = "sale__sale_date" if side == "item" else "sale_return__return_date"
    if group in ("day", "week", "month"):
        return {"g_key": _period(datef, group, True), "g_label": Value("")}
    if group == "product":
        return {"g_key": F(p + "product_id"), "g_label": F(p + "description")}
    if group == "category":
        return {"g_key": Coalesce(F(p + "product__category_id"), Value(0)),
                "g_label": Coalesce(F(p + "product__category__name"), Value("Uncategorised"))}
    if group == "customer":
        return {"g_key": Coalesce(F(sale + "customer_id"), Value(0)),
                "g_label": Coalesce(F(sale + "customer__name"), Value("Walk-in customers"))}
    if group == "user":
        return {"g_key": F(sale + "created_by_id"), "g_label": F(sale + "created_by__full_name")}
    raise ValueError(group)


def _filter_sales_items(qs, f, side):
    p = "" if side == "item" else "sale_item__"
    sale = "sale__" if side == "item" else "sale_item__sale__"
    if f.product:
        qs = qs.filter(**{p + "product": f.product})
    if f.category:
        qs = qs.filter(**{p + "product__category": f.category})
    if f.customer:
        qs = qs.filter(**{sale + "customer": f.customer})
    if f.user:
        qs = qs.filter(**{sale + "created_by": f.user})
    return qs


def sales_grouped(business, f, group, title=None):
    from sales.models import SaleItem, SaleReturnItem

    period = group if group in ("day", "week", "month") else "day"
    items = SaleItem.objects.filter(sale__business=business, sale__status="completed",
                                    sale__sale_date__date__range=(f.start, f.end))
    rets = SaleReturnItem.objects.filter(sale_return__business=business, sale_return__sale__status="completed",
                                         sale_return__return_date__date__range=(f.start, f.end))
    items, rets = _filter_sales_items(items, f, "item"), _filter_sales_items(rets, f, "ret")
    spec_i = _sale_spec(group, "item", period)
    sold = {}
    for r in (items.annotate(**spec_i).values("g_key", "g_label").annotate(
            n=Count("sale_id", distinct=True), qty=_sum("quantity"),
            net=_sum(_wrap(F("quantity") * F("unit_price") - F("discount"), MONEY)),
            cost=_sum(_wrap(F("quantity") * F("unit_cost"), MONEY)))):
        sold[r["g_key"]] = r
    returned = {}
    for r in (rets.annotate(**_sale_spec(group, "ret", period)).values("g_key", "g_label").annotate(
            qty=_sum("quantity"), net=_sum("net_amount"),
            cost=_sum(_wrap(F("quantity") * F("sale_item__unit_cost"), MONEY)))):
        returned[r["g_key"]] = r
    rows = []
    for key in sorted(set(sold) | set(returned), key=lambda k: (str(k) if not isinstance(k, date) else k.isoformat())):
        s, rt = sold.get(key, {}), returned.get(key, {})
        label = _fmt_period(key, period) if group in ("day", "week", "month") else (s.get("g_label") or rt.get("g_label"))
        net, rnet = s.get("net", ZERO), rt.get("net", ZERO)
        cost, rcost = s.get("cost", ZERO), rt.get("cost", ZERO)
        rows.append({"group": label, "sales": s.get("n", 0), "qty": s.get("qty", ZERO) - rt.get("qty", ZERO),
                     "gross": net, "returned": rnet, "net": net - rnet, "cost": cost - rcost,
                     "profit": net - rnet - (cost - rcost)})
    if group not in ("day", "week", "month"):
        rows.sort(key=lambda r: r["net"], reverse=True)
    for r in rows:
        r["margin"] = (r["profit"] / r["net"] * 100) if r["net"] else None
    labels = {"day": "Day", "week": "Week", "month": "Month", "product": "Product", "category": "Category",
              "customer": "Customer", "user": "Sold by"}
    cols = [Column("group", labels[group]), Column("sales", "Sales", INT), Column("qty", "Qty (net)", QTY),
            Column("gross", "Sales value", MONEY_KIND), Column("returned", "Returns", MONEY_KIND),
            Column("net", "Net sales", MONEY_KIND), Column("cost", "Cost of goods", MONEY_KIND),
            Column("profit", "Gross profit", MONEY_KIND), Column("margin", "Margin %", "pct")]
    totals = _totals(rows, ["sales", "qty", "gross", "returned", "net", "cost", "profit"])
    totals["margin"] = (totals["profit"] / totals["net"] * 100) if totals["net"] else None
    return ReportResult(title or "Sales", cols, rows, totals, f.describe(),
                        note="Net sales exclude tax and are after discounts; returns are counted in the period they were made.")


def sales_detail(business, f, title="Sales lines"):
    from sales.models import SaleItem

    qs = _filter_sales_items(SaleItem.objects.filter(
        sale__business=business, sale__status="completed", sale__sale_date__date__range=(f.start, f.end)), f, "item")
    qs = qs.annotate(net=_wrap(F("quantity") * F("unit_price") - F("discount"), MONEY)).order_by("-sale__sale_date", "-id")
    rows, trunc = [], False
    for i in qs.values("sale__sale_date", "sale__number", "sale__customer__name", "sale__created_by__full_name",
                       "description", "quantity", "unit_price", "discount", "net")[:MAX_DETAIL_ROWS + 1]:
        rows.append({"date": timezone.localtime(i["sale__sale_date"]).date(), "number": i["sale__number"],
                     "customer": i["sale__customer__name"] or "Walk-in", "user": i["sale__created_by__full_name"],
                     "product": i["description"], "qty": i["quantity"], "price": i["unit_price"],
                     "discount": i["discount"], "net": i["net"]})
    if len(rows) > MAX_DETAIL_ROWS:
        rows, trunc = rows[:MAX_DETAIL_ROWS], True
    cols = [Column("date", "Date", DATE), Column("number", "Sale"), Column("customer", "Customer"), Column("user", "Sold by"),
            Column("product", "Product"), Column("qty", "Qty", QTY), Column("price", "Unit price", MONEY_KIND),
            Column("discount", "Discount", MONEY_KIND), Column("net", "Net amount", MONEY_KIND)]
    return ReportResult(title, cols, rows, _totals(rows, ["qty", "discount", "net"]), f.describe(), trunc)


def sales_by_payment_method(business, f):
    return payments_grouped(business, f, "method", title="Sales by payment method", direction="in",
                            note="Money received per method (includes later payments on credit sales).")


# =============================================================== generic grouped datasets
def _datasets():
    from expenses.models import Expense
    from inventory.models import StockMovement
    from payments.models import Payment
    from purchases.models import GoodsReceiptItem

    return {
        "purchases": dict(
            model=GoodsReceiptItem, base=lambda b: Q(receipt__business=b), date="receipt__received_on", is_dt=False,
            specs={"supplier": ("receipt__purchase__supplier_id", "receipt__purchase__supplier__name", "Supplier"),
                   "product": ("purchase_item__product_id", "purchase_item__product__name", "Product"),
                   "category": ("purchase_item__product__category_id", "purchase_item__product__category__name", "Category")},
            measures=[("lines", "Lines", INT, lambda: Count("id")), ("qty", "Quantity", QTY, lambda: _sum("quantity")),
                      ("amount", "Value received", MONEY_KIND, lambda: _sum(_wrap(F("quantity") * F("unit_cost"), MONEY)))],
            filters={"supplier": "receipt__purchase__supplier", "product": "purchase_item__product",
                     "category": "purchase_item__product__category", "user": "receipt__received_by"}),
        "stock": dict(
            model=StockMovement, base=lambda b: Q(business=b), date="created_at", is_dt=True,
            specs={"product": ("product_id", "product__name", "Product"), "category": ("product__category_id", "product__category__name", "Category"),
                   "type": ("movement_type", "movement_type", "Movement type"), "user": ("created_by_id", "created_by__full_name", "User")},
            measures=[("lines", "Movements", INT, lambda: Count("id")),
                      ("qty_in", "Stock in", QTY, lambda: _sum("quantity", filter=Q(quantity__gt=0))),
                      ("qty_out", "Stock out", QTY, lambda: _sum(-F("quantity"), filter=Q(quantity__lt=0))),
                      ("net", "Net change", QTY, lambda: _sum("quantity"))],
            filters={"product": "product", "category": "product__category", "user": "created_by", "movement_type": "movement_type"}),
        "payments": dict(
            model=Payment, base=lambda b: Q(business=b, status="posted"), date="paid_on", is_dt=False,
            specs={"method": ("method_id", "method__name", "Payment method"), "customer": ("customer_id", "customer__name", "Customer"),
                   "supplier": ("supplier_id", "supplier__name", "Supplier"), "direction": ("direction", "direction", "Direction"),
                   "user": ("created_by_id", "created_by__full_name", "Recorded by")},
            measures=[("lines", "Payments", INT, lambda: Count("id")),
                      ("money_in", "Money in", MONEY_KIND, lambda: _sum("amount", filter=Q(direction="in"))),
                      ("money_out", "Money out", MONEY_KIND, lambda: _sum("amount", filter=Q(direction="out")))],
            filters={"method": "method", "customer": "customer", "supplier": "supplier", "user": "created_by", "movement_type": "direction"}),
        "expenses": dict(
            model=Expense, base=lambda b: Q(business=b, status="posted"), date="expense_date", is_dt=False,
            specs={"category": ("category_id", "category__name", "Category"), "method": ("method_id", "method__name", "Payment method"),
                   "user": ("recorded_by_id", "recorded_by__full_name", "Recorded by")},
            measures=[("lines", "Expenses", INT, lambda: Count("id")), ("amount", "Amount", MONEY_KIND, lambda: _sum("amount"))],
            filters={"method": "method", "user": "recorded_by"}),
    }


def dataset_groups(dataset):
    if dataset == "sales":
        return ["product", "category", "customer", "user", "day", "week", "month"]
    return list(_datasets()[dataset]["specs"]) + ["day", "week", "month"]


def grouped(dataset, business, f, group, title=None, **fixed):
    ds = _datasets()[dataset]
    qs = ds["model"].objects.filter(ds["base"](business))
    date_f = ds["date"]
    qs = qs.filter(**{f"{date_f}__date__range" if ds["is_dt"] else f"{date_f}__range": (f.start, f.end)})
    for name, path in ds["filters"].items():
        val = fixed.get(name, getattr(f, name, None))
        if val:
            qs = qs.filter(**{path: val})
    if group in ("day", "week", "month"):
        qs = qs.annotate(g_key=_period(date_f, group, ds["is_dt"]), g_label=Value(""))
        label_col = {"day": "Day", "week": "Week", "month": "Month"}[group]
    else:
        key, label, label_col = ds["specs"][group]
        qs = qs.annotate(g_key=Coalesce(F(key), Value(0)) if key.endswith("_id") else F(key), g_label=Coalesce(F(label), Value("—")))
    agg = {k: fn() for k, _, _, fn in ds["measures"]}
    rows = []
    for r in qs.values("g_key", "g_label").annotate(**agg).order_by("g_key"):
        if group in ("day", "week", "month"):
            label = _fmt_period(r["g_key"], group)
        elif group in ("type", "direction"):
            label = str(r["g_label"]).replace("_", " ").capitalize()
        else:
            label = str(r["g_label"])
        row = {"group": label}
        row.update({k: r[k] for k, *_ in ds["measures"]})
        rows.append(row)
    sort_key = ds["measures"][-1][0] if dataset != "stock" else "lines"
    if group not in ("day", "week", "month"):
        rows.sort(key=lambda r: r.get(sort_key) or 0, reverse=True)
    cols = [Column("group", label_col)] + [Column(k, lbl, kind) for k, lbl, kind, _ in ds["measures"]]
    return ReportResult(title or dataset.title(), cols, rows, _totals(rows, [k for k, *_ in ds["measures"]]), f.describe())


def payments_grouped(business, f, group, title="Payments", direction=None, note=""):
    res = grouped("payments", business, f, group, title, movement_type=direction or f.movement_type)
    res.note = note
    return res


def detail(dataset, business, f, title=None):
    """Row-level listing for any builder dataset."""
    if dataset == "sales":
        return sales_detail(business, f, title or "Sales lines")
    ds = _datasets()[dataset]
    qs = ds["model"].objects.filter(ds["base"](business))
    date_f = ds["date"]
    qs = qs.filter(**{f"{date_f}__date__range" if ds["is_dt"] else f"{date_f}__range": (f.start, f.end)})
    for name, path in ds["filters"].items():
        val = getattr(f, name, None)
        if val:
            qs = qs.filter(**{path: val})
    spec = {
        "purchases": ([("receipt__received_on", "Date", DATE), ("receipt__number", "Receipt", TEXT), ("receipt__purchase__supplier__name", "Supplier", TEXT),
                       ("purchase_item__product__name", "Product", TEXT), ("quantity", "Qty", QTY), ("unit_cost", "Unit cost", MONEY_KIND)], "-receipt__received_on"),
        "stock": ([("created_at", "Date", DATE), ("product__name", "Product", TEXT), ("movement_type", "Type", TEXT), ("quantity", "Change", QTY),
                   ("balance_after", "Balance after", QTY), ("source_ref", "Reference", TEXT), ("reason", "Reason", TEXT), ("created_by__full_name", "By", TEXT)], "-created_at"),
        "payments": ([("paid_on", "Date", DATE), ("direction", "Direction", TEXT), ("customer__name", "Customer", TEXT), ("supplier__name", "Supplier", TEXT),
                      ("method__name", "Method", TEXT), ("amount", "Amount", MONEY_KIND), ("reference", "Reference", TEXT)], "-paid_on"),
        "expenses": ([("expense_date", "Date", DATE), ("number", "Number", TEXT), ("category__name", "Category", TEXT), ("description", "Description", TEXT),
                      ("method__name", "Method", TEXT), ("amount", "Amount", MONEY_KIND)], "-expense_date"),
    }[dataset]
    fields, order = spec
    rows, trunc = [], False
    for r in qs.order_by(order, "-id").values(*[p for p, _, _ in fields])[:MAX_DETAIL_ROWS + 1]:
        row = {}
        for path, _, kind in fields:
            v = r[path]
            if kind == DATE and v is not None and hasattr(v, "hour"):
                v = timezone.localtime(v).date()
            row[path] = v if v is not None else ""
            if path == "movement_type" and v:
                row[path] = str(v).replace("_", " ").capitalize()
        rows.append(row)
    if len(rows) > MAX_DETAIL_ROWS:
        rows, trunc = rows[:MAX_DETAIL_ROWS], True
    cols = [Column(p, lbl, k) for p, lbl, k in fields]
    totals = _totals(rows, [p for p, _, k in fields if k in (MONEY_KIND,) or (k == QTY and dataset in ("sales", "purchases"))])
    return ReportResult(title or f"{dataset.title()} detail", cols, rows, totals, f.describe(), trunc)


# =============================================================== inventory & finance standard reports
def stock_current(business, f, only=None):
    from products.models import Product

    qs = Product.objects.filter(business=business, is_active=True).select_related("category", "unit", "stock")
    if f.category:
        qs = qs.filter(category=f.category)
    if f.product:
        qs = qs.filter(pk=f.product.pk)
    if only == "low":
        qs = qs.filter(stock__quantity__gt=0, stock__quantity__lte=F("reorder_level"))
    elif only == "out":
        qs = qs.filter(stock__quantity__lte=0)
    rows = []
    for p in qs.order_by("category__name", "name"):
        q = p.stock.quantity
        rows.append({"product": p.display_name, "sku": p.sku, "category": p.category.name if p.category else "—", "unit": p.unit.abbreviation,
                     "qty": q, "reorder": p.reorder_level, "cost": p.purchase_price, "value": max(q, ZERO) * p.purchase_price,
                     "retail": max(q, ZERO) * p.selling_price})
    cols = [Column("product", "Product"), Column("sku", "SKU"), Column("category", "Category"), Column("unit", "Unit"),
            Column("qty", "On hand", QTY), Column("reorder", "Reorder level", QTY), Column("cost", "Cost price", MONEY_KIND),
            Column("value", "Stock value (cost)", MONEY_KIND), Column("retail", "Value (retail)", MONEY_KIND)]
    title = {"low": "Low stock", "out": "Out of stock"}.get(only, "Current stock")
    return ReportResult(title, cols, rows, _totals(rows, ["value", "retail"]), f"As at {timezone.localdate():%d %b %Y}" + (f" · Category: {f.category}" if f.category else ""))


def stock_valuation(business, f):
    from inventory.models import StockLevel

    qs = StockLevel.objects.filter(business=business, product__is_active=True, quantity__gt=0)
    if f.category:
        qs = qs.filter(product__category=f.category)
    data = qs.annotate(g=Coalesce(F("product__category__name"), Value("Uncategorised"))).values("g").annotate(
        items=Count("id"), qty=_sum("quantity"), cost=_sum(_wrap(F("quantity") * F("product__purchase_price"), MONEY)),
        retail=_sum(_wrap(F("quantity") * F("product__selling_price"), MONEY))).order_by("-cost")
    rows = [{"group": r["g"], "items": r["items"], "qty": r["qty"], "cost": r["cost"], "retail": r["retail"],
             "margin": r["retail"] - r["cost"]} for r in data]
    cols = [Column("group", "Category"), Column("items", "Products", INT), Column("qty", "Units", QTY), Column("cost", "Value at cost", MONEY_KIND),
            Column("retail", "Value at retail", MONEY_KIND), Column("margin", "Potential margin", MONEY_KIND)]
    return ReportResult("Stock valuation", cols, rows, _totals(rows, ["items", "qty", "cost", "retail", "margin"]),
                        f"As at {timezone.localdate():%d %b %Y}", note="Valued at current moving-average cost. Negative stock is excluded.")


def stock_variance(business, f):
    from inventory.models import StockCountLine

    qs = StockCountLine.objects.filter(count__business=business, count__status="approved", count__reviewed_at__date__range=(f.start, f.end)).exclude(counted_quantity=None)
    if f.product:
        qs = qs.filter(product=f.product)
    if f.category:
        qs = qs.filter(product__category=f.category)
    rows = []
    for l in qs.select_related("count", "product").order_by("-count__reviewed_at", "product__name"):
        v = l.variance
        if v == 0:
            continue
        rows.append({"date": timezone.localtime(l.count.reviewed_at).date(), "count": l.count.number, "product": l.product.display_name,
                     "expected": l.expected_quantity, "counted": l.counted_quantity, "variance": v, "value": v * l.unit_cost, "reason": l.reason})
    cols = [Column("date", "Approved", DATE), Column("count", "Count"), Column("product", "Product"), Column("expected", "Expected", QTY),
            Column("counted", "Physical", QTY), Column("variance", "Variance", QTY), Column("value", "Variance value", MONEY_KIND), Column("reason", "Reason")]
    return ReportResult("Stock variance (approved counts)", cols, rows, _totals(rows, ["value"]), f.describe())


def expiring_stock(business, f, days=90):
    from purchases.models import GoodsReceiptItem

    limit = timezone.localdate() + timedelta(days=days)
    qs = GoodsReceiptItem.objects.filter(receipt__business=business, expiry_date__isnull=False, expiry_date__lte=limit)
    if f.product:
        qs = qs.filter(purchase_item__product=f.product)
    rows = []
    for i in qs.select_related("purchase_item__product", "receipt").order_by("expiry_date"):
        left = (i.expiry_date - timezone.localdate()).days
        rows.append({"product": i.purchase_item.product.display_name, "batch": i.batch_number or "—", "expiry": i.expiry_date,
                     "days": left, "received": i.quantity, "receipt": i.receipt.number,
                     "status": "Expired" if left < 0 else "Expiring soon"})
    cols = [Column("product", "Product"), Column("batch", "Batch"), Column("expiry", "Expiry", DATE), Column("days", "Days left", INT),
            Column("received", "Qty received", QTY), Column("receipt", "Delivery"), Column("status", "Status")]
    return ReportResult("Expiring and expired batches", cols, rows, {}, f"Batches expiring within {days} days",
                        note="Quantities are as received into stock; batch-level sales are not tracked, so check shelf stock before acting.")


def purchase_history(business, f):
    from purchases.models import PurchaseOrder

    qs = PurchaseOrder.objects.filter(business=business, order_date__range=(f.start, f.end)).exclude(status="cancelled").select_related("supplier")
    if f.supplier:
        qs = qs.filter(supplier=f.supplier)
    rows = [{"date": p.order_date, "number": p.number, "supplier": p.supplier.name, "status": p.get_status_display(), "total": p.total,
             "received": p.received_value, "paid": p.amount_paid, "due": p.balance_due} for p in qs.order_by("-order_date", "-id")[:MAX_DETAIL_ROWS]]
    cols = [Column("date", "Date", DATE), Column("number", "Purchase"), Column("supplier", "Supplier"), Column("status", "Status"),
            Column("total", "Ordered", MONEY_KIND), Column("received", "Received", MONEY_KIND), Column("paid", "Paid", MONEY_KIND), Column("due", "Still owed", MONEY_KIND)]
    return ReportResult("Purchase history", cols, rows, _totals(rows, ["total", "received", "paid", "due"]), f.describe())


def balances(business, f, party):
    from contacts.models import Customer, Supplier

    model = Customer if party == "customer" else Supplier
    qs = model.objects.filter(business=business).annotate(bal=Coalesce(Sum("ledger_entries__amount"), Value(ZERO), output_field=MONEY)).exclude(bal=0)
    if party == "customer" and f.customer:
        qs = qs.filter(pk=f.customer.pk)
    if party == "supplier" and f.supplier:
        qs = qs.filter(pk=f.supplier.pk)
    today = timezone.localdate()
    overdue = {}
    if party == "customer":
        from sales.models import Sale
        for r in Sale.objects.filter(business=business, status="completed", balance_due__gt=0, due_date__lt=today).values("customer_id").annotate(t=Sum("balance_due")):
            overdue[r["customer_id"]] = r["t"]
    rows = [{"code": c.code, "name": c.name, "phone": c.phone or "—", "balance": c.bal, **({"overdue": overdue.get(c.pk, ZERO)} if party == "customer" else {})}
            for c in qs.order_by("-bal")]
    cols = [Column("code", "Code"), Column("name", "Customer" if party == "customer" else "Supplier"), Column("phone", "Phone"),
            Column("balance", "Owed to us" if party == "customer" else "We owe", MONEY_KIND)]
    keys = ["balance"]
    if party == "customer":
        cols.append(Column("overdue", "Of which overdue", MONEY_KIND))
        keys.append("overdue")
    return ReportResult("Customer credits (receivables)" if party == "customer" else "Supplier balances (payables)", cols, rows, _totals(rows, keys),
                        f"As at {today:%d %b %Y}", note="Negative amounts mean the party holds credit with you.")


def profit_by_product(business, f):
    res = sales_grouped(business, f, "product", "Profit by product")
    res.rows.sort(key=lambda r: r["profit"], reverse=True)
    return res


def sales_over_time(business, f):
    return sales_grouped(business, f, f.period, f"Sales by {f.period}")


# =============================================================== registry
@dataclass
class ReportDef:
    key: str
    title: str
    group: str
    description: str
    filters: tuple
    build: object
    permission: str = "reports.view"
    finance: bool = False


def _preset(dataset, group, title, **fixed):
    return lambda b, f: grouped(dataset, b, f, group, title, **fixed)


def daily_summary_report(business, f):
    from .services import daily_result

    return daily_result(business, f.end)


REPORTS = [
    ReportDef("daily-summary", "Daily summary", "Daily", "Everything that happened on one day: sales, returns, money received, expenses, purchases, stock and balances.", ("day",), daily_summary_report, permission="finance.view", finance=True),
    ReportDef("stock-current", "Current stock", "Inventory", "Quantity on hand and value for every product.", ("category", "product"), lambda b, f: stock_current(b, f)),
    ReportDef("stock-valuation", "Stock valuation", "Inventory", "Value of stock by category at cost and retail.", ("category",), stock_valuation, finance=True),
    ReportDef("stock-movement", "Stock movement", "Inventory", "Every stock ledger entry in the period.", ("date", "category", "product", "movement_type", "user"), lambda b, f: detail("stock", b, f, "Stock movement")),
    ReportDef("stock-low", "Low stock", "Inventory", "Products at or below their reorder level.", ("category",), lambda b, f: stock_current(b, f, "low")),
    ReportDef("stock-out", "Out of stock", "Inventory", "Products with no stock.", ("category",), lambda b, f: stock_current(b, f, "out")),
    ReportDef("stock-variance", "Stock variance", "Inventory", "Differences found in approved stock counts.", ("date", "category", "product"), stock_variance),
    ReportDef("stock-adjustments", "Stock adjustments", "Inventory", "Manual adjustments and count corrections by product.", ("date", "category", "product", "user"),
              lambda b, f: grouped("stock", b, f, "product", "Stock adjustments", movement_type=f.movement_type or "adjustment")),
    ReportDef("stock-damaged", "Damaged stock", "Inventory", "Stock written off as damaged.", ("date", "category", "product", "user"),
              lambda b, f: grouped("stock", b, f, "product", "Damaged stock", movement_type="damage")),
    ReportDef("stock-lost", "Lost stock", "Inventory", "Stock written off as lost.", ("date", "category", "product", "user"),
              lambda b, f: grouped("stock", b, f, "product", "Lost stock", movement_type="loss")),
    ReportDef("stock-expiry", "Expiring stock", "Inventory", "Received batches that are expired or close to expiry.", ("product",), expiring_stock),
    ReportDef("sales-time", "Sales over time", "Sales", "Daily, weekly or monthly sales.", ("date", "period", "customer", "user", "category", "product"), sales_over_time),
    ReportDef("sales-product", "Sales by product", "Sales", "Which products sell and what they earn.", ("date", "category", "customer", "user"), lambda b, f: sales_grouped(b, f, "product", "Sales by product")),
    ReportDef("sales-category", "Sales by category", "Sales", "Sales grouped by product category.", ("date", "customer", "user"), lambda b, f: sales_grouped(b, f, "category", "Sales by category")),
    ReportDef("sales-user", "Sales by user", "Sales", "Sales per team member.", ("date", "category", "customer"), lambda b, f: sales_grouped(b, f, "user", "Sales by user")),
    ReportDef("sales-customer", "Sales by customer", "Sales", "Sales per customer.", ("date", "category", "user"), lambda b, f: sales_grouped(b, f, "customer", "Sales by customer")),
    ReportDef("sales-method", "Sales by payment method", "Sales", "Money received by payment method.", ("date", "method"), sales_by_payment_method),
    ReportDef("purchases-supplier", "Purchases by supplier", "Purchases", "Value of goods received per supplier.", ("date", "category", "product"), _preset("purchases", "supplier", "Purchases by supplier")),
    ReportDef("purchases-date", "Purchases by date", "Purchases", "Goods received over time.", ("date", "period", "supplier", "category", "product"),
              lambda b, f: grouped("purchases", b, f, f.period, f"Purchases by {f.period}")),
    ReportDef("purchases-history", "Purchase history", "Purchases", "Every purchase order with what is still owed.", ("date", "supplier"), purchase_history),
    ReportDef("expenses", "Expenses", "Finance", "Operating expenses by category.", ("date", "method", "user"), _preset("expenses", "category", "Expenses by category"), permission="finance.view", finance=True),
    ReportDef("customer-credits", "Customer credits", "Finance", "Who owes you money and what is overdue.", ("customer",), lambda b, f: balances(b, f, "customer"), permission="finance.view", finance=True),
    ReportDef("supplier-balances", "Supplier balances", "Finance", "What you owe suppliers.", ("supplier",), lambda b, f: balances(b, f, "supplier"), permission="finance.view", finance=True),
    ReportDef("payments", "Payments", "Finance", "Money in and out by payment method.", ("date", "method", "customer", "supplier", "user"), lambda b, f: payments_grouped(b, f, "method", "Payments by method"), permission="finance.view", finance=True),
    ReportDef("profit-product", "Profit by product", "Finance", "Gross profit and margin per product (estimate from recorded costs).", ("date", "category", "customer", "user"), profit_by_product, permission="finance.view", finance=True),
]
REPORT_MAP = {r.key: r for r in REPORTS}
