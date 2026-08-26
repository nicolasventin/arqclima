from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Usuario del sistema. El rol se modela con el Group nativo de Django:
    cada usuario pertenece a exactamente un grupo/rol (Administrador, Ventas
    y Presupuestos, Service y Repuestos, Depósito, Técnico de Campo).

    Los permisos puntuales que el administrador le otorgue a un usuario por
    fuera de su rol se guardan en el campo nativo `user_permissions`
    (heredado de AbstractUser): es la tabla de overrides individuales
    pedida en las reglas de negocio, ya provista por Django. User.has_perm()
    combina automáticamente permisos de rol + overrides.
    """

    telefono = models.CharField(max_length=30, blank=True)

    class Meta:
        permissions = [
            ("manage_permissions", "Puede administrar roles y permisos de usuarios"),
        ]

    @property
    def rol(self):
        group = self.groups.first()
        return group.name if group else None

    def __str__(self):
        return self.get_full_name() or self.username
