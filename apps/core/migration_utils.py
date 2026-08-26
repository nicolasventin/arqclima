from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions


def crear_permisos_pendientes(apps, app_labels):
    """
    Fuerza la creación de los Permission (incluyendo los permisos custom
    declarados en Meta.permissions) de los apps indicados, para usar dentro
    de una migración de datos.

    Es necesario porque Django los crea recién en la señal post_migrate,
    que se dispara DESPUÉS de que terminan TODAS las migraciones de un
    'migrate' — si una migración de datos de ese mismo 'migrate' necesita
    asignar esos Permission a un Group, todavía no existen. Este helper
    los crea a mano en ese momento.
    """
    for app_config in django_apps.get_app_configs():
        if app_config.label not in app_labels:
            continue
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
