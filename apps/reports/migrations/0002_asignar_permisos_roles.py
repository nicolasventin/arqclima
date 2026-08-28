from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Comercial es el dominio de Rodrigo, con supervisión de Diego — no de
# Gabriel/Contri/Andrés. Los montos confidenciales (regla explícita de
# Diego: nadie más ve el total de ingresos) son exclusivos de
# Administrador, sin excepción de rol.
CODENAMES_POR_ROL = {
    "Administrador": ["view_reporte_comercial", "view_montos_confidenciales"],
    "Ventas y Presupuestos": ["view_reporte_comercial"],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["reports"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="reports", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
