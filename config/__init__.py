try:  # Celery is optional in development; background jobs run when it is installed and configured.
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ImportError:  # pragma: no cover
    pass
