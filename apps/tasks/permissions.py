def puede_gestionar_tareas(user):
    """
    Crear tareas y asignarlas/reasignarlas a cualquier empleado —
    Administrador, Ventas y Presupuestos (Rodrigo) y Service y
    Repuestos (Gabriel), según la matriz de roles. El permiso es del
    rol, no de "haber creado" la tarea: cualquiera de los tres puede
    reasignar una tarea creada por otro (ej. Diego corrige una
    asignación que hizo Rodrigo por error).
    """
    return user.has_perm("tasks.add_tarea")


def puede_actualizar_estado(user, tarea):
    """
    Mover el estado de una tarea: quien la gestiona (ver arriba) sobre
    cualquier tarea, o el propio asignado sobre la suya (Contri/Andrés
    no tienen add_tarea/change_tarea, pero sí pueden mover su propia
    tarea por Pendiente/En proceso/Completada libremente).
    """
    if puede_gestionar_tareas(user):
        return True
    return tarea.asignado_a_id == user.id


def queryset_tareas_visibles(user, queryset):
    """
    Alcance de visibilidad por rol:
    - Diego (view_all_tareas): todo el equipo, sin filtrar.
    - Rodrigo/Gabriel (puede_gestionar_tareas): lo que asignaron +
      lo que tienen asignado a ellos mismos.
    - Contri/Andrés: solo lo que tienen asignado a ellos mismos.
    """
    if user.has_perm("tasks.view_all_tareas"):
        return queryset
    if puede_gestionar_tareas(user):
        from django.db.models import Q

        return queryset.filter(Q(asignado_por=user) | Q(asignado_a=user))
    return queryset.filter(asignado_a=user)
