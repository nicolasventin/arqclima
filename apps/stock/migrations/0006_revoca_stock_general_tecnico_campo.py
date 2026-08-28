from django.db import migrations

# Cierra la decisión 42bis (Etapa 7/8), que quedaba explícitamente abierta
# para la Etapa 9: Andrés (Técnico de Campo) tenía manage_stock_general de
# forma GENERAL, sin acotar a sus propios trabajos, porque el modelo
# Trabajo no existía todavía cuando se le dio ese permiso en la Etapa 7.
#
# Ahora que existe apps.jobs, su necesidad real — enviar material a su
# trabajo y devolver el sobrante ("vuelve a stock", regla de negocio 11)
# — ya está cubierta por enviar_material()/registrar_sobrante(),
# gateadas por jobs.manage_ejecucion_propia + chequeo de fila
# (material.trabajo.tecnico_asignado_id == user.id): eso SÍ lo acota a
# sus propios trabajos, algo que stock.manage_stock_general nunca pudo
# expresar (la pantalla cruda de stock no sabe nada de Trabajo). Se
# revoca el permiso crudo en vez de intentar acotarlo, porque ya es
# innecesario: la vía correcta para Andrés siempre pasa por jobs.
CODENAME = "manage_stock_general"
ROL = "Técnico de Campo"


def revocar_permiso(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.get(name=ROL)
    permiso = Permission.objects.get(content_type__app_label="stock", codename=CODENAME)
    grupo.permissions.remove(permiso)


def reasignar_permiso(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.get(name=ROL)
    permiso = Permission.objects.get(content_type__app_label="stock", codename=CODENAME)
    grupo.permissions.add(permiso)


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0005_movimientostock_linea_orden_compra_and_more"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(revocar_permiso, reasignar_permiso),
    ]
