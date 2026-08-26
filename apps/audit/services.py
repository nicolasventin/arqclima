from django.contrib.contenttypes.models import ContentType

from .models import AuditLog


def log_action(user, action, obj=None, detail=""):
    """
    Registra una acción de auditoría. Pensado para llamarse desde cualquier
    módulo futuro, por ejemplo:

        log_action(request.user, "create_presupuesto", presupuesto, "...")

    `user` puede ser None (ej. login fallido, o una acción del sistema).
    """
    content_type = None
    object_id = None
    objeto_repr = ""

    if obj is not None:
        content_type = ContentType.objects.get_for_model(obj)
        object_id = str(obj.pk)
        objeto_repr = str(obj)[:255]

    usuario = user if (user is not None and getattr(user, "is_authenticated", True)) else None

    return AuditLog.objects.create(
        usuario=usuario,
        accion=action,
        detalle=detail,
        content_type=content_type,
        object_id=object_id,
        objeto_repr=objeto_repr,
    )
