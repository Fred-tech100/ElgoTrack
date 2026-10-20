from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from businesses.services import create_business
from inventory.models import StockMovement
from inventory.services import manual_adjustment
from payments.models import PaymentMethod
from products.models import Product, Unit

PASSWORD = "S3cure-pass-phrase!"


def make_business(name="Alpha Hardware", email="owner@alpha.test", **kwargs):
    business, owner = create_business(name=name, business_type="hardware", owner_email=email,
                                      owner_name="Alpha Owner", owner_password=PASSWORD, **kwargs)
    return business, owner


def make_user(business, role_key, email):
    from accounts.models import Role
    from core.permissions import DEFAULT_ROLES

    role = Role.objects.get(business=business, name=DEFAULT_ROLES[role_key]["name"])
    return User.objects.create_user(email=email, password=PASSWORD, full_name=email.split("@")[0], business=business, role=role)


def make_product(business, owner, name="Cement 50kg", price="30000", cost="25000", stock="100", **kw):
    unit = Unit.objects.filter(business=business, name="Piece").first()
    p = Product.objects.create(business=business, name=name, unit=unit, selling_price=Decimal(price),
                               purchase_price=Decimal(cost), **kw)
    if Decimal(stock) > 0:
        manual_adjustment(business=business, user=owner, product=p, movement_type=StockMovement.OPENING, quantity=Decimal(stock))
    return p


class TenantTestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.business, self.owner = make_business()
        self.other, self.other_owner = make_business("Beta Pharmacy", "owner@beta.test")
        self.cash = PaymentMethod.objects.get(business=self.business, name="Cash")
        self.momo = PaymentMethod.objects.get(business=self.business, name="Mobile Money")
        self.product = make_product(self.business, self.owner)

    def login(self, user):
        self.client.force_login(user)

    def stock(self, product=None):
        product = product or self.product
        product.stock.refresh_from_db()
        return product.stock.quantity
