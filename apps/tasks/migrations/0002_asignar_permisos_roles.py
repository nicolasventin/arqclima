from django.db import migrations

from apps.core.migration_utils import crear_permisos_pendientes

# Diego, Rodrigo y Gabriel pueden crear tareas y asignarlas/reasignarlas
# a cualquier empleado (regla de negocio 14). Contri y Andrés solo ven
# las propias (alcance resuelto en la vista, no acá) y pueden moverlas
# de estado sin tener add_tarea/change_tarea — ver
# apps.tasks.permissions.puede_actualizar_estado.
CODENAMES_POR_ROL = {
    "Administrador": ["view_tarea", "add_tarea", "change_tarea", "view_all_tareas"],
    "Ventas y Presupuestos": ["view_tarea", "add_tarea", "change_tarea"],
    "Service y Repuestos": ["view_tarea", "add_tarea", "change_tarea"],
    "Depósito": ["view_tarea"],
    "Técnico de Campo": ["view_tarea"],
}


def asignar_permisos(apps, schema_editor):
    crear_permisos_pendientes(apps, ["tasks"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, codenames in CODENAMES_POR_ROL.items():
        grupo = Group.objects.get(name=nombre_rol)
        permisos = Permission.objects.filter(
            content_type__app_label="tasks", codename__in=codenames
        )
        grupo.permissions.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0001_initial"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos, revertir),
    ]
