from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Ver stock: todos los roles (regla de negocio, matriz de permisos de
# Etapa 7). El resto de los codenames son custom (ver Meta.permissions
# en MovimientoStock) porque "entrada/salida en general" y
# "entrada/salida en repuestos" necesitan poder otorgarse por
# separado, cosa que los add/change/delete/view genéricos de Django no
# permiten expresar.
#
# Andrés (Técnico de Campo) tiene manage_stock_general de forma
# GENERAL, no acotada a sus propios trabajos — ampliación real
# respecto de la matriz de permisos original (que solo lo listaba para
# "salida"): la regla de negocio 11 dice que el sobrante que retira
# "vuelve a stock" (una entrada), así que necesita las dos acciones.
# Cuando exista el modelo Trabajo (Etapa 8), evaluar si conviene
# acotar esto a "solo movimientos relacionados con sus propios
# trabajos asignados" en vez de dejarlo general para siempre.
CODENAMES_POR_ROL = {
    "Administrador": [
        "view_movimientostock", "add_movimientostock",
        "ajustar_stock_general", "manage_stock_minimo",
    ],
    "Ventas y Presupuestos": ["view_movimientostock"],
    "Service y Repuestos": ["view_movimientostock", "manage_stock_repuestos"],
    "Depósito": ["view_movimientostock", "manage_stock_general", "ajustar_stock_general"],
    "Técnico de Campo": ["view_movimientostock", "manage_stock_general"],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["stock"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="stock", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0002_trigger_movimientostock_inmutable"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
