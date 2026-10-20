from django.urls import Resolver404, resolve


def _nav(request):
    user = request.user
    if not user.is_authenticated or user.is_superuser or request.business is None:
        return []
    can = user.has_business_perm
    groups = [
        ("Overview", [("Dashboard", "core:home", True, "core")]),
        ("Sales", [
            ("New sale", "sales:create", can("sales.create"), "sales_create"),
            ("Sales", "sales:list", can("sales.view"), "sales"),
            ("Customers", "customers:list", can("contacts.view"), "customers"),
            ("Invoices & receipts", "invoices:list", can("invoices.view"), "invoices"),
            ("Verify a document", "verify:scan", can("invoices.view"), "verify"),
        ]),
        ("Stock", [
            ("Products", "products:list", can("products.view"), "products"),
            ("Stock levels", "inventory:stock", can("inventory.view"), "inventory"),
            ("Stock counts", "inventory:count_list", can("inventory.count") or can("inventory.approve"), "counts"),
            ("Purchases", "purchases:list", can("purchases.view"), "purchases"),
            ("Suppliers", "suppliers:list", can("contacts.view"), "suppliers"),
        ]),
        ("Money", [
            ("Payments", "payments:list", can("payments.view") or can("payments.receive"), "payments"),
            ("Expenses", "expenses:list", can("expenses.view"), "expenses"),
            ("Financial summary", "reports:summary", can("finance.view"), "reports_summary"),
            ("Reports", "reports:index", can("reports.view") or can("reports.builder") or can("finance.view"), "reports"),
        ]),
        ("Administration", [
            ("Users & roles", "accounts:users", can("users.manage") or can("roles.manage"), "accounts"),
            ("Settings", "businesses:settings", can("settings.manage"), "businesses"),
            ("Audit log", "audit:list", can("audit.view"), "audit"),
        ]),
    ]
    match = request.resolver_match
    current = match.namespace if match else ""
    if match and match.view_name == "reports:summary":
        current = "reports_summary"
    if match and match.view_name == "sales:create":
        current = "sales_create"
    if match and match.view_name == "inventory:count_list" or (match and match.url_name and match.url_name.startswith("count_")):
        current = "counts"
    result = []
    for title, items in groups:
        visible = [
            {"label": label, "url": name, "active": current == key or (key == "core" and current == "")}
            for label, name, allowed, key in items
            if allowed
        ]
        if visible:
            result.append({"title": title, "items": visible})
    return result


def tenant(request):
    business = getattr(request, "business", None)
    unread = 0
    user = request.user
    if business is not None and user.is_authenticated and not user.is_superuser:
        from django.core.cache import cache

        from notifications.services import sync_notifications, unread_count

        key = f"notif-sync:{business.pk}"
        if not cache.get(key):  # refresh at most every 10 minutes even without a scheduler
            cache.set(key, 1, 600)
            try:
                sync_notifications(business)
            except Exception:  # never break a page because alerts failed
                import logging
                logging.getLogger(__name__).exception("Notification sync failed")
        unread = unread_count(user)
    return {"business": business, "nav_groups": _nav(request), "unread_notifications": unread}
