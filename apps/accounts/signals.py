from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from apps.audit.services import log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action(user=user, action="login", detail="Inicio de sesión exitoso")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    log_action(user=user, action="logout", detail="Cierre de sesión")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, **kwargs):
    log_action(
        user=None,
        action="login_failed",
        detail=f"Intento fallido para usuario '{credentials.get('username')}'",
    )
