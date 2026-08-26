from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

CODENAMES_POR_ROL = {
    "Administrador": [
        "view_marca", "add_marca", "change_marca", "delete_marca",
        "view_categoria", "add_categoria", "change_categoria", "delete_categoria",
        "view_proveedor", "add_proveedor", "change_proveedor", "delete_proveedor",
        "view_producto", "add_producto", "change_producto", "delete_producto",
        "view_productoproveedor", "add_productoproveedor",
        "change_productoproveedor", "delete_productoproveedor",
        "manage_repuestos",
    ],
    "Ventas y Presupuestos": [
        "view_producto", "view_marca", "view_categoria",
        "view_proveedor", "add_proveedor", "change_proveedor",
    ],
    "Service y Repuestos": [
        "view_producto", "manage_repuestos",
        "view_marca", "view_categoria", "view_proveedor",
    ],
    "Depósito": [
        "view_producto", "view_marca", "view_categoria", "view_proveedor",
    ],
    "Técnico de Campo": [
        "view_producto", "view_marca", "view_categoria", "view_proveedor",
    ],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["catalog"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="catalog", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
