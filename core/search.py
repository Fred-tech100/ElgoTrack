from django.db.models import Q
from django.views.generic import TemplateView

from .mixins import TenantRequiredMixin


class SearchView(TenantRequiredMixin, TemplateView):
    """Global search across products, parties and documents, limited to what the user may see."""

    template_name = "search.html"
    LIMIT = 8

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        q = self.request.GET.get("q", "").strip()
        b, user = self.request.business, self.request.user
        can = user.has_business_perm
        sections = []
        if len(q) >= 2:
            from contacts.models import Customer, Supplier
            from expenses.models import Expense
            from invoices.models import Invoice, Receipt
            from payments.models import Payment
            from products.models import Product
            from purchases.models import PurchaseOrder
            from sales.models import Sale

            L = self.LIMIT

            def add(title, perm, items):
                if can(perm):
                    items = list(items[:L])
                    if items:
                        sections.append((title, items))

            add("Products", "products.view", [(p.display_name, f"{p.sku}" + (f" · {p.barcode}" if p.barcode else ""), f"/products/{p.pk}/") for p in Product.objects.filter(business=b).filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q) | Q(brand__icontains=q))])
            add("Customers", "contacts.view", [(c.name, f"{c.code} · {c.phone}", f"/customers/{c.pk}/") for c in Customer.objects.filter(business=b).filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(code__icontains=q) | Q(email__icontains=q))])
            add("Suppliers", "contacts.view", [(c.name, f"{c.code} · {c.phone}", f"/suppliers/{c.pk}/") for c in Supplier.objects.filter(business=b).filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(code__icontains=q))])
            add("Sales", "sales.view", [(s.number, f"{s.customer.name if s.customer else 'Walk-in'} · {s.sale_date:%d %b %Y}", f"/sales/{s.pk}/") for s in Sale.objects.filter(business=b).filter(Q(number__icontains=q) | Q(customer__name__icontains=q)).select_related("customer")])
            add("Invoices", "invoices.view", [(i.number, f"Sale {i.sale.number}", f"/documents/invoice/{i.pk}/") for i in Invoice.objects.filter(business=b, number__icontains=q).select_related("sale")])
            add("Receipts", "invoices.view", [(r.number, f"{r.issued_at:%d %b %Y}", f"/documents/receipt/{r.pk}/") for r in Receipt.objects.filter(business=b, number__icontains=q)])
            add("Purchases", "purchases.view", [(p.number, p.supplier.name, f"/purchases/{p.pk}/") for p in PurchaseOrder.objects.filter(business=b).filter(Q(number__icontains=q) | Q(supplier_reference__icontains=q) | Q(supplier__name__icontains=q)).select_related("supplier")])
            add("Payments (by reference)", "payments.view", [(p.reference, f"{p.paid_on:%d %b %Y}", "/payments/?q=" + p.reference) for p in Payment.objects.filter(business=b, reference__icontains=q)])
            add("Expenses", "expenses.view", [(e.number, e.description, f"/expenses/{e.pk}/") for e in Expense.objects.filter(business=b).filter(Q(number__icontains=q) | Q(description__icontains=q))])
        ctx.update(q=q, sections=sections)
        return ctx
