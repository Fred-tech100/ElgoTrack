from django.db import transaction
from django.utils import timezone

from audit.services import log_action
from core.exceptions import BusinessRuleError
from core.models import next_number

from .models import Expense


@transaction.atomic
def record_expense(*, business, user, category, amount, method, description, expense_date=None, attachment=None):
    if amount <= 0:
        raise BusinessRuleError("Expense amount must be greater than zero.")
    expense = Expense.objects.create(
        business=business, number=next_number(business, "expense", "EXP"), category=category, amount=amount,
        method=method, description=description, expense_date=expense_date or timezone.localdate(),
        attachment=attachment or "", recorded_by=user,
    )
    log_action("expense.create", business=business, user=user, obj=expense, after={"amount": amount, "category": category.name})
    return expense


@transaction.atomic
def void_expense(expense, user, reason):
    expense = Expense.objects.select_for_update().get(pk=expense.pk, business=user.business)
    if expense.status == Expense.VOID:
        raise BusinessRuleError("This expense is already void.")
    if not reason.strip():
        raise BusinessRuleError("Please give a reason for voiding the expense.")
    expense.status, expense.voided_by, expense.voided_at, expense.void_reason = Expense.VOID, user, timezone.now(), reason[:300]
    expense.save()
    log_action("expense.void", business=expense.business, user=user, obj=expense, message=reason)
    return expense
