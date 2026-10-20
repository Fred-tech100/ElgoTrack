"""Financial summaries. Every figure is derived from transactional records."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

ZERO = Decimal("0")


def _sum(qs, field):
    return qs.aggregate(t=Coalesce(Sum(field), ZERO))["t"]


def financial_summary(business, start, end):
    from contacts.models import Customer, Supplier
    from expenses.models import Expense
    from payments.models import Payment, PartyLedgerEntry
    from purchases.models import GoodsReceiptItem, PurchaseReturn
    from sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

    sales = Sale.objects.filter(business=business, status=Sale.COMPLETED, sale_date__date__gte=start, sale_date__date__lte=end)
    returns = SaleReturn.objects.filter(business=business, sale__status=Sale.COMPLETED,
                                        return_date__date__gte=start, return_date__date__lte=end)
    gross_sales = _sum(sales, "subtotal")
    discounts = _sum(sales, "discount_total")
    tax_collected = _sum(sales, "tax_total")
    return_net = _sum(returns, "net_total")
    return_tax = _sum(returns, "tax_total")
    net_sales = gross_sales - discounts - return_net

    cost_expr = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    cogs_sold = _sum(SaleItem.objects.filter(sale__in=sales).annotate(c=cost_expr), "c")
    ret_cost_expr = ExpressionWrapper(F("quantity") * F("sale_item__unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    cogs_returned = _sum(SaleReturnItem.objects.filter(sale_return__in=returns).annotate(c=ret_cost_expr), "c")
    cogs = cogs_sold - cogs_returned
    gross_profit = net_sales - cogs

    expenses = Expense.objects.filter(business=business, status=Expense.POSTED, expense_date__gte=start, expense_date__lte=end)
    total_expenses = _sum(expenses, "amount")
    by_category = list(expenses.values("category__name").annotate(total=Sum("amount")).order_by("-total"))

    grn_cost = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    purchases = _sum(GoodsReceiptItem.objects.filter(receipt__business=business, receipt__received_on__gte=start,
                                                      receipt__received_on__lte=end).annotate(c=grn_cost), "c")
    purchase_returns = _sum(PurchaseReturn.objects.filter(business=business, return_date__gte=start, return_date__lte=end), "total")

    posted = Payment.objects.filter(business=business, status=Payment.POSTED, paid_on__gte=start, paid_on__lte=end)
    money_in = _sum(posted.filter(direction=Payment.IN), "amount")
    paid_suppliers = _sum(posted.filter(direction=Payment.OUT, purpose=Payment.SUPPLIER_PAYMENT), "amount")
    refunds = _sum(posted.filter(direction=Payment.OUT, purpose=Payment.SALE_REFUND), "amount")

    receivables = _sum(PartyLedgerEntry.objects.filter(business=business, customer__isnull=False), "amount")
    payables = _sum(PartyLedgerEntry.objects.filter(business=business, supplier__isnull=False), "amount")

    return {
        "gross_sales": gross_sales, "discounts": discounts, "returns": return_net, "net_sales": net_sales,
        "tax_collected": tax_collected - return_tax, "cogs": cogs, "gross_profit": gross_profit,
        "gross_margin": (gross_profit / net_sales * 100) if net_sales else None,
        "expenses": total_expenses, "expenses_by_category": by_category,
        "net_operating_result": gross_profit - total_expenses,
        "purchases": purchases - purchase_returns,
        "money_in": money_in, "paid_suppliers": paid_suppliers, "refunds": refunds, "expenses_paid": total_expenses,
        "net_cash_movement": money_in - paid_suppliers - refunds - total_expenses,
        "receivables": receivables, "payables": payables,
        "sale_count": sales.count(),
    }


def top_balances(business, party="customer", limit=8):
    from contacts.models import Customer, Supplier

    model = Customer if party == "customer" else Supplier
    return (model.objects.filter(business=business)
            .annotate(bal=Coalesce(Sum("ledger_entries__amount"), ZERO))
            .filter(bal__gt=0).order_by("-bal")[:limit])


def dashboard_metrics(business, include_finance=True):
    from expenses.models import Expense
    from inventory.models import StockLevel
    from purchases.models import GoodsReceiptItem
    from sales.models import Sale

    today = timezone.localdate()
    data = {}
    todays_sales = Sale.objects.filter(business=business, status=Sale.COMPLETED, sale_date__date=today)
    data["today_sales"] = _sum(todays_sales, "total")
    data["today_sale_count"] = todays_sales.count()
    levels = StockLevel.objects.filter(business=business, product__is_active=True)
    data["out_of_stock"] = levels.filter(quantity__lte=0).count()
    data["low_stock"] = (levels.filter(quantity__gt=0, quantity__lte=F("product__reorder_level"))
                         .select_related("product").order_by("quantity")[:8])
    data["low_stock_count"] = levels.filter(quantity__gt=0, quantity__lte=F("product__reorder_level")).count()
    start = today - timedelta(days=13)
    daily = dict(Sale.objects.filter(business=business, status=Sale.COMPLETED, sale_date__date__gte=start)
                 .annotate(d=TruncDate("sale_date")).values("d").annotate(t=Sum("total")).values_list("d", "t"))
    series = [(start + timedelta(days=i), daily.get(start + timedelta(days=i), ZERO)) for i in range(14)]
    peak = max((v for _, v in series), default=ZERO) or Decimal("1")
    data["trend"] = [{"date": d, "total": v, "pct": int(v / peak * 100)} for d, v in series]
    data["recent_sales"] = Sale.objects.filter(business=business).select_related("customer").order_by("-sale_date")[:6]
    if include_finance:
        cost = ExpressionWrapper(F("quantity") * F("product__purchase_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
        data["stock_value"] = _sum(levels.filter(quantity__gt=0).annotate(v=cost), "v")
        data["today_expenses"] = _sum(Expense.objects.filter(business=business, status=Expense.POSTED, expense_date=today), "amount")
        gcost = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
        data["today_purchases"] = _sum(GoodsReceiptItem.objects.filter(receipt__business=business, receipt__received_on=today).annotate(c=gcost), "c")
        from payments.models import PartyLedgerEntry
        data["receivables"] = _sum(PartyLedgerEntry.objects.filter(business=business, customer__isnull=False), "amount")
        data["payables"] = _sum(PartyLedgerEntry.objects.filter(business=business, supplier__isnull=False), "amount")
        data["overdue_count"] = Sale.objects.filter(business=business, status=Sale.COMPLETED, balance_due__gt=0, due_date__lt=today).count()
    return data


def onboarding_steps(business):
    from inventory.models import StockMovement
    from products.models import Category, Product

    return [
        ("Add your business details", bool(business.address and business.phone), "businesses:settings"),
        ("Invite your team", business.users.count() > 1, "accounts:users"),
        ("Create product categories", Category.objects.filter(business=business).exists(), "products:categories"),
        ("Add your products", Product.objects.filter(business=business).exists(), "products:create"),
        ("Set opening stock", StockMovement.objects.filter(business=business, movement_type="opening").exists(), "inventory:adjust"),
    ]


def daily_summary(business, day):
    """Everything that happened on one day, in plain business terms. Figures come from the same records as the other reports."""
    from django.db.models import Count

    from expenses.models import Expense
    from inventory.models import StockLevel, StockMovement
    from payments.models import PartyLedgerEntry, Payment
    from purchases.models import GoodsReceiptItem, PurchaseReturn
    from sales.models import Sale, SaleItem, SaleReturn

    sales = Sale.objects.filter(business=business, status=Sale.COMPLETED, sale_date__date=day)
    cancelled = Sale.objects.filter(business=business, status=Sale.CANCELLED, cancelled_at__date=day)
    returns = SaleReturn.objects.filter(business=business, return_date__date=day, sale__status=Sale.COMPLETED)
    gross, disc, tax = _sum(sales, "subtotal"), _sum(sales, "discount_total"), _sum(sales, "tax_total")
    ret_net = _sum(returns, "net_total")
    cost_expr = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    ret_cost = ExpressionWrapper(F("quantity") * F("sale_item__unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    from sales.models import SaleReturnItem

    cogs = _sum(SaleItem.objects.filter(sale__in=sales).annotate(c=cost_expr), "c") - _sum(SaleReturnItem.objects.filter(sale_return__in=returns).annotate(c=ret_cost), "c")
    pay = Payment.objects.filter(business=business, status=Payment.POSTED, paid_on=day)
    money_in = pay.filter(direction=Payment.IN)
    by_method = list(money_in.values("method__name").annotate(total=Sum("amount"), n=Count("id")).order_by("-total"))
    refunds = _sum(pay.filter(direction=Payment.OUT, purpose=Payment.SALE_REFUND), "amount")
    supplier_paid = _sum(pay.filter(direction=Payment.OUT, purpose=Payment.SUPPLIER_PAYMENT), "amount")
    on_account = _sum(money_in.filter(Q(sale__isnull=True) | Q(sale__sale_date__date__lt=day)), "amount")
    expenses = Expense.objects.filter(business=business, status=Expense.POSTED, expense_date=day)
    exp_total = _sum(expenses, "amount")
    grn = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    received = _sum(GoodsReceiptItem.objects.filter(receipt__business=business, receipt__received_on=day).annotate(c=grn), "c")
    purchase_ret = _sum(PurchaseReturn.objects.filter(business=business, return_date=day), "total")
    moves = StockMovement.objects.filter(business=business, created_at__date=day)
    written_off = moves.filter(movement_type__in=("damage", "loss")).aggregate(t=Coalesce(Sum("quantity"), Value(Decimal("0")), output_field=DecimalField(max_digits=20, decimal_places=3)))["t"]
    levels = StockLevel.objects.filter(business=business, product__is_active=True)
    stock_cost = ExpressionWrapper(F("quantity") * F("product__purchase_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
    top = list(SaleItem.objects.filter(sale__in=sales).values("description").annotate(q=Sum("quantity"), net=Sum(ExpressionWrapper(F("quantity") * F("unit_price") - F("discount"), output_field=DecimalField(max_digits=18, decimal_places=2)))).order_by("-net")[:5])
    unpaid_today = _sum(sales, "balance_due")
    receivables = _sum(PartyLedgerEntry.objects.filter(business=business, customer__isnull=False), "amount")
    payables = _sum(PartyLedgerEntry.objects.filter(business=business, supplier__isnull=False), "amount")
    overdue = Sale.objects.filter(business=business, status=Sale.COMPLETED, balance_due__gt=0, due_date__lt=day)
    in_total = _sum(money_in, "amount")
    return {
        "day": day,
        "sales": {"count": sales.count(), "gross": gross, "discounts": disc, "returns": ret_net, "net": gross - disc - ret_net, "tax": tax,
                  "cogs": cogs, "profit": gross - disc - ret_net - cogs, "cancelled": cancelled.count(), "cancelled_value": _sum(cancelled, "total"), "unpaid": unpaid_today},
        "cash": {"in": in_total, "by_method": by_method, "refunds": refunds, "supplier_paid": supplier_paid, "expenses": exp_total,
                 "net": in_total - refunds - supplier_paid - exp_total, "on_account": on_account},
        "purchases": {"received": received, "returned": purchase_ret, "supplier_paid": supplier_paid},
        "expenses": {"total": exp_total, "count": expenses.count(), "by_category": list(expenses.values("category__name").annotate(total=Sum("amount")).order_by("-total"))},
        "stock": {"movements": moves.count(), "written_off": abs(written_off), "value": _sum(levels.filter(quantity__gt=0).annotate(v=stock_cost), "v"),
                  "low": levels.filter(quantity__gt=0, quantity__lte=F("product__reorder_level")).count(), "out": levels.filter(quantity__lte=0).count()},
        "balances": {"receivables": receivables, "payables": payables, "overdue_count": overdue.count(), "overdue_value": _sum(overdue, "balance_due")},
        "top": top,
    }


def daily_result(business, day):
    """Flatten the daily summary for CSV / Excel / PDF export."""
    from .engine import Column, INT, MONEY_KIND, ReportResult, TEXT

    d = daily_summary(business, day)
    rows = []

    def add(section, item, amount=None, count=None):
        rows.append({"section": section, "item": item, "count": count, "amount": amount})

    s = d["sales"]
    add("Sales", "Sales made", count=s["count"])
    for label, key in (("Sales value (before discounts)", "gross"), ("Discounts", "discounts"), ("Returns", "returns"), ("Net sales (excl. tax)", "net"),
                       ("Cost of goods sold", "cogs"), ("Gross profit", "profit"), ("Tax collected", "tax"), ("Unpaid on today's sales", "unpaid")):
        add("Sales", label, s[key])
    add("Sales", "Sales cancelled", s["cancelled_value"], s["cancelled"])
    for m in d["cash"]["by_method"]:
        add("Money received", m["method__name"], m["total"], m["n"])
    add("Money received", "Total received", d["cash"]["in"])
    add("Money received", "Of which on older credit / account", d["cash"]["on_account"])
    for label, val in (("Customer refunds", d["cash"]["refunds"]), ("Paid to suppliers", d["cash"]["supplier_paid"]), ("Expenses", d["cash"]["expenses"]), ("Net cash movement", d["cash"]["net"])):
        add("Cash position", label, val)
    add("Purchases", "Goods received", d["purchases"]["received"])
    add("Purchases", "Returned to suppliers", d["purchases"]["returned"])
    for c in d["expenses"]["by_category"]:
        add("Expenses", c["category__name"], c["total"])
    add("Stock", "Stock movements recorded", count=d["stock"]["movements"])
    add("Stock", "Units written off (damage/loss)", count=int(d["stock"]["written_off"]))
    add("Stock", "Stock value at cost", d["stock"]["value"])
    add("Stock", "Low stock products", count=d["stock"]["low"])
    add("Stock", "Out of stock products", count=d["stock"]["out"])
    add("Balances", "Customers owe you", d["balances"]["receivables"])
    add("Balances", "Overdue customer balances", d["balances"]["overdue_value"], d["balances"]["overdue_count"])
    add("Balances", "You owe suppliers", d["balances"]["payables"])
    for t in d["top"]:
        add("Best sellers", t["description"], t["net"], int(t["q"]))
    cols = [Column("section", "Section", TEXT), Column("item", "Item", TEXT), Column("count", "Count", INT), Column("amount", "Amount", MONEY_KIND)]
    return ReportResult(f"Daily summary {day:%d %b %Y}", cols, rows, {}, f"{day:%A, %d %B %Y}")
