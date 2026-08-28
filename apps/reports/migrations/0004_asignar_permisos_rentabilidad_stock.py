from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Rentabilidad es exclusivo de Diego (regla explícita, mismo criterio que
# view_montos_confidenciales: nadie más ve rentabilidad agregada). Stock es
# compartido por los tres roles que controlan stock (Etapa 7/8): Diego,
# Contri (stock general) y Gabriel (stock de repuestos) — Rodrigo y Andrés
# no lo tienen, ninguno de los dos gestiona stock de forma directa.
CODENAMES_POR_ROL = {
    "Administrador": ["view_reporte_rentabilidad", "view_reporte_stock"],
    "Depósito": ["view_reporte_stock"],
    "Service y Repuestos": ["view_reporte_stock"],
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
        ("reports", "0003_agrega_permisos_rentabilidad_stock"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
