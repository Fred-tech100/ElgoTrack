class BusinessRuleError(Exception):
    """A user-facing rule violation (shown to the user as a message)."""


class InsufficientStock(BusinessRuleError):
    pass
