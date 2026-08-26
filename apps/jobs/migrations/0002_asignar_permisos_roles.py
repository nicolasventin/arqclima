from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Diego cubre todas las transiciones vía change_trabajo (ver
# apps.jobs.permissions.puede_cambiar_estado_trabajo) — no hace falta
# darle además manage_preparacion/manage_ejecucion_propia.
#
# Gabriel no tiene NINGÚN permiso acá: su módulo de service/repuestos
# es autónomo y separado del resto de catálogo/obra, y su rol original
# no menciona trabajos en ningún lado.
CODENAMES_POR_ROL = {
    "Administrador": ["view_trabajo", "add_trabajo", "change_trabajo"],
    "Ventas y Presupuestos": ["view_trabajo", "add_trabajo"],
    "Depósito": ["view_trabajo", "manage_preparacion"],
    "Técnico de Campo": ["view_trabajo", "manage_ejecucion_propia"],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["jobs"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="jobs", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
