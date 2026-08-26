from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Solo Diego y Rodrigo tocan clientes según sus roles definidos (Diego:
# acceso total; Rodrigo: "clientes" en su acceso explícito). El resto de
# los roles no tiene permisos acá todavía — si más adelante Trabajos
# necesita que Contri/Andrés/Gabriel vean datos de cliente, se agrega en
# una migración de esa etapa.
CODENAMES_POR_ROL = {
    "Administrador": [
        "view_cliente", "add_cliente", "change_cliente", "delete_cliente",
    ],
    "Ventas y Presupuestos": [
        "view_cliente", "add_cliente", "change_cliente",
    ],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["clients"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="clients", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
