from django.db import migrations

# Los 5 roles reales de ARQCLIMA. Se hardcodean acá (en vez de importar
# apps.accounts.roles) porque una migración debe ser una foto autocontenida:
# si el archivo roles.py cambia en el futuro, esta migración no debe
# cambiar de comportamiento retroactivamente.
ROLES = [
    "Administrador",
    "Ventas y Presupuestos",
    "Service y Repuestos",
    "Depósito",
    "Técnico de Campo",
]


def crear_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in ROLES:
        Group.objects.get_or_create(name=nombre)


def eliminar_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_grupos, eliminar_grupos),
    ]
