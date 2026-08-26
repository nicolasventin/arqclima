from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# No se le da change_historialcosto ni delete_historialcosto a NADIE,
# ni siquiera a Administrador: el modelo es append-only (ver docstring de
# HistorialCosto). El trigger de Postgres de la migración anterior es el
# resguardo real; esto es una segunda señal, a nivel de permisos.
CODENAMES_POR_ROL = {
    "Administrador": [
        "view_historialcosto", "add_historialcosto",
        "manage_margenes",
    ],
    "Ventas y Presupuestos": [
        "view_historialcosto",
    ],
    "Service y Repuestos": [
        "view_precio_repuestos", "manage_costos_repuestos",
    ],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["pricing"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="pricing", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0002_trigger_historialcosto_inmutable"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
