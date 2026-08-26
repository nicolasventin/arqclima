from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes


def asignar(apps, schema_editor):
    crear_permisos_pendientes(apps, ["accounts"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo = Group.objects.get(name="Administrador")
    permiso = Permission.objects.get(
        codename="manage_permissions", content_type__app_label="accounts"
    )
    grupo.permissions.add(permiso)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar, revertir),
    ]
