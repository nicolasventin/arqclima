from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Revertir un Aceptado es una decisión de negocio (un Aceptado es la
# puerta de entrada a que nazca un Trabajo en la Etapa 8), no un trámite
# de rutina del ciclo diario de Rodrigo — por eso este permiso queda
# SOLO para Administrador, a diferencia del resto de los permisos de
# quotes (ver 0002_asignar_permisos_roles).


def asignar_permiso(apps, schema_editor):
    crear_permisos_pendientes(apps, ["quotes"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.get(name="Administrador")
    permiso = Permission.objects.get(
        codename="revert_presupuesto_aceptado", content_type__app_label="quotes"
    )
    grupo.permissions.add(permiso)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0006_trigger_bloquear_edicion_fuera_de_borrador"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permiso, revertir),
    ]
