from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action("auth.login", user=user, business=user.business, obj=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        log_action("auth.logout", user=user, business=user.business, obj=user)


@receiver(user_login_failed)
def on_failed(sender, credentials, request=None, **kwargs):
    log_action("auth.login_failed", message=f"Failed sign-in for {credentials.get('username', '')[:120]}")
