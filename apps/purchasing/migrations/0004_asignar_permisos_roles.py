from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Regla de negocio 7: Rodrigo, Gabriel y Andrés pueden crear órdenes de
# compra; Diego tiene que aprobarlas (approve_ordendecompra, bloqueo
# real) antes de que se envíen al proveedor. Diego también cubre
# cancelar (change_ordendecompra, mismo criterio "bypass total" que
# change_trabajo en jobs). Contri solo ve — recibir mercadería pasa
# por el permiso de Stock (puede_registrar_entrada_salida), no por un
# permiso de purchasing.
CODENAMES_POR_ROL = {
    "Administrador": [
        "view_ordendecompra", "add_ordendecompra", "change_ordendecompra", "approve_ordendecompra",
    ],
    "Ventas y Presupuestos": ["view_ordendecompra", "add_ordendecompra"],
    "Service y Repuestos": ["view_ordendecompra", "add_ordendecompra"],
    "Depósito": ["view_ordendecompra"],
    "Técnico de Campo": ["view_ordendecompra", "add_ordendecompra"],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["purchasing"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="purchasing", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0003_trigger_linea_mismo_proveedor"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
