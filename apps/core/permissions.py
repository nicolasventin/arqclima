"""
Helpers para chequear permisos combinando rol (Group) + overrides individuales.

Django ya resuelve esto de forma nativa: User.has_perm() evalúa la unión de
los permisos del grupo del usuario (su rol) y los permisos asignados
directamente al usuario (sus overrides, en user.user_permissions). Estas
funciones son wrappers finos para tener un único punto de entrada en el
proyecto, en vez de llamar a la API de Django desde cada vista.
"""


def user_has_perm(user, perm_codename):
    if not user.is_authenticated:
        return False
    return user.has_perm(perm_codename)


def user_has_any_perm(user, perm_codenames):
    return any(user_has_perm(user, p) for p in perm_codenames)


def get_user_role(user):
    """Nombre del rol (grupo) principal del usuario, o None si no tiene."""
    group = user.groups.first()
    return group.name if group else None
