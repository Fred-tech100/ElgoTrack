"""Permission catalogue and default role templates.

Permissions are plain codenames stored per role (``accounts.RolePermission``).
They are enforced server-side by ``core.mixins.TenantRequiredMixin``.
"""

PERMISSION_GROUPS = [
    ("Products", [
        ("products.view", "View products"),
        ("products.manage", "Create and edit products, categories and units"),
    ]),
    ("Inventory", [
        ("inventory.view", "View stock levels and movement history"),
        ("inventory.adjust", "Record opening stock, adjustments, damage and loss"),
        ("inventory.count", "Start and enter stock counts"),
        ("inventory.approve", "Approve or reject stock counts"),
    ]),
    ("Sales", [
        ("sales.view", "View sales"),
        ("sales.create", "Record sales"),
        ("sales.discount", "Give discounts and change prices at the till"),
        ("sales.return", "Process sale returns"),
        ("sales.cancel", "Cancel sales"),
    ]),
    ("Purchases", [
        ("purchases.view", "View purchases"),
        ("purchases.create", "Create purchase orders"),
        ("purchases.receive", "Receive stock and record purchase returns"),
        ("purchases.cancel", "Cancel purchase orders"),
    ]),
    ("Customers and suppliers", [
        ("contacts.view", "View customers and suppliers"),
        ("contacts.manage", "Create and edit customers and suppliers"),
    ]),
    ("Payments and finance", [
        ("payments.view", "View payments"),
        ("payments.receive", "Receive customer payments"),
        ("payments.pay_supplier", "Record supplier payments"),
        ("payments.reverse", "Reverse payments"),
        ("expenses.view", "View expenses"),
        ("expenses.create", "Record expenses"),
        ("expenses.void", "Void expenses"),
        ("finance.view", "View financial summaries and balances"),
        ("invoices.view", "View invoices and receipts"),
    ]),
    ("Reports", [
        ("reports.view", "View and export operational reports"),
        ("reports.builder", "Build, save and export custom reports"),
    ]),
    ("Administration", [
        ("users.manage", "Manage users"),
        ("roles.manage", "Manage roles and permissions"),
        ("settings.manage", "Change business settings"),
        ("audit.view", "View the audit log"),
    ]),
]

ALL_CODES = [code for _, items in PERMISSION_GROUPS for code, _ in items]
PERMISSION_LABELS = {code: label for _, items in PERMISSION_GROUPS for code, label in items}

_MANAGER_EXCLUDED = {"users.manage", "roles.manage", "settings.manage"}

DEFAULT_ROLES = {
    "owner": {"name": "Business Owner", "description": "Full access to the business.", "codes": ALL_CODES},
    "manager": {
        "name": "Manager",
        "description": "Operations, approvals and reports.",
        "codes": [c for c in ALL_CODES if c not in _MANAGER_EXCLUDED],
    },
    "salesperson": {
        "name": "Salesperson",
        "description": "Sales, customers and receiving customer payments.",
        "codes": [
            "products.view", "inventory.view", "sales.view", "sales.create", "contacts.view",
            "contacts.manage", "payments.receive", "invoices.view",
        ],
    },
    "stock_manager": {
        "name": "Stock Manager",
        "description": "Inventory, stock counts and receiving purchases.",
        "codes": [
            "products.view", "products.manage", "inventory.view", "inventory.adjust", "inventory.count",
            "purchases.view", "purchases.create", "purchases.receive", "contacts.view", "reports.view",
        ],
    },
    "accountant": {
        "name": "Accountant",
        "description": "Payments, expenses and financial reports.",
        "codes": [
            "products.view", "inventory.view", "sales.view", "purchases.view", "contacts.view", "reports.view", "reports.builder",
            "payments.view", "payments.receive", "payments.pay_supplier", "payments.reverse",
            "expenses.view", "expenses.create", "expenses.void", "finance.view", "invoices.view",
        ],
    },
}
