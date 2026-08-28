from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Clientes es dominio compartido de venta (mismo criterio que Comercial:
# Diego + Rodrigo). Empleados es exclusivo de Diego — es una vista sobre
# el trabajo de otros, distinta categoría de sensibilidad que Comercial/
# Rentabilidad/Stock/Clientes (ninguno de esos reportes muestra actividad
# individual de una persona puntual).
CODENAMES_POR_ROL = {
    "Administrador": ["view_reporte_clientes", "view_reporte_empleados"],
    "Ventas y Presupuestos": ["view_reporte_clientes"],
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
        ("reports", "0005_agrega_permisos_clientes_empleados"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
