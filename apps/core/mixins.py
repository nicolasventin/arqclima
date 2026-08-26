from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin


class PermisoRequeridoMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """
    Login obligatorio + chequeo de permiso. El chequeo de permiso
    (PermissionRequiredMixin -> user.has_perm) ya combina rol (Group) y
    overrides individuales de forma nativa. Ante falta de permiso devuelve
    403 (ver templates/403.html).
    """

    raise_exception = True
