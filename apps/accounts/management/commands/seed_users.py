import secrets
import string
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.accounts.roles import (
    ADMINISTRADOR,
    DEPOSITO,
    ROLES,
    SERVICE_Y_REPUESTOS,
    TECNICO_DE_CAMPO,
    VENTAS_Y_PRESUPUESTOS,
)

USUARIOS = [
    {
        "username": "diego",
        "first_name": "Diego",
        "rol": ADMINISTRADOR,
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "username": "rodrigo",
        "first_name": "Rodrigo",
        "rol": VENTAS_Y_PRESUPUESTOS,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "username": "gabriel",
        "first_name": "Gabriel",
        "rol": SERVICE_Y_REPUESTOS,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "username": "contri",
        "first_name": "Contri",
        "rol": DEPOSITO,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "username": "andres",
        "first_name": "Andrés",
        "rol": TECNICO_DE_CAMPO,
        "is_staff": False,
        "is_superuser": False,
    },
]


def generar_password():
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(12))


class Command(BaseCommand):
    help = (
        "Crea los roles (grupos) y los 5 usuarios reales de ARQCLIMA con su "
        "rol correspondiente. Es idempotente: se puede correr varias veces "
        "sin duplicar nada. Las contraseñas generadas se guardan en "
        "credenciales.txt (no versionado). Usar --reset-passwords para "
        "regenerar la contraseña de todos, incluso si ya existían."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Regenera la contraseña de todos los usuarios, existan o no.",
        )

    def handle(self, *args, **options):
        reset_passwords = options["reset_passwords"]

        for nombre_rol in ROLES:
            Group.objects.get_or_create(name=nombre_rol)
        self.stdout.write(self.style.SUCCESS(f"Roles listos: {', '.join(ROLES)}"))

        credenciales = []

        for datos in USUARIOS:
            grupo = Group.objects.get(name=datos["rol"])

            usuario, creado = User.objects.get_or_create(
                username=datos["username"],
                defaults={
                    "first_name": datos["first_name"],
                    "is_staff": datos["is_staff"],
                    "is_superuser": datos["is_superuser"],
                },
            )

            if creado or reset_passwords:
                password = generar_password()
                usuario.set_password(password)
                usuario.save()
                credenciales.append((usuario.username, password))

            usuario.groups.set([grupo])

        self.stdout.write(self.style.SUCCESS("Usuarios listos, cada uno con su rol asignado."))

        if credenciales:
            ruta_archivo = settings.BASE_DIR / "credenciales.txt"
            contenido = [
                "Credenciales generadas para ARQCLIMA — NO subir a git.",
                f"Generado: {datetime.now():%Y-%m-%d %H:%M}",
                "Pedile a cada persona que la cambie apenas entre, desde",
                "el menú de usuario > 'Cambiar contraseña'.",
                "",
            ]
            contenido += [f"{username}: {password}" for username, password in credenciales]
            ruta_archivo.write_text("\n".join(contenido) + "\n")

            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(f"Credenciales guardadas en: {ruta_archivo}")
            )
            for username, password in credenciales:
                self.stdout.write(f"  {username}: {password}")
        else:
            self.stdout.write("No se generaron contraseñas nuevas (ya existían todos los usuarios).")
