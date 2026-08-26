from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Presupuesto no se borra nunca vía permisos de rol (se cancela cambiando
# el estado a "Cancelado", conservando el número y la trazabilidad); por
# eso ningún rol salvo Administrador tiene delete_presupuesto. Secciones
# e ítems sí se pueden borrar mientras se arma un presupuesto en borrador.
# PlantillaCondiciones (la plantilla maestra, no el texto ya copiado a un
# presupuesto puntual) sigue el mismo criterio que márgenes en pricing:
# solo Diego la administra: Rodrigo solo puede verla para elegir cuál usar.
CODENAMES_POR_ROL = {
    "Administrador": [
        "view_presupuesto", "add_presupuesto", "change_presupuesto", "delete_presupuesto",
        "view_seccionpresupuesto", "add_seccionpresupuesto",
        "change_seccionpresupuesto", "delete_seccionpresupuesto",
        "view_itempresupuesto", "add_itempresupuesto",
        "change_itempresupuesto", "delete_itempresupuesto",
        "view_plantillacondiciones", "add_plantillacondiciones",
        "change_plantillacondiciones", "delete_plantillacondiciones",
    ],
    "Ventas y Presupuestos": [
        "view_presupuesto", "add_presupuesto", "change_presupuesto",
        "view_seccionpresupuesto", "add_seccionpresupuesto",
        "change_seccionpresupuesto", "delete_seccionpresupuesto",
        "view_itempresupuesto", "add_itempresupuesto",
        "change_itempresupuesto", "delete_itempresupuesto",
        "view_plantillacondiciones",
    ],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["quotes"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="quotes", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
