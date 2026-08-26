from .models import EstadoTrabajo

ESTADOS_PREPARACION = {EstadoTrabajo.PREPARANDO_MATERIALES, EstadoTrabajo.LISTO}
ESTADOS_EJECUCION = {EstadoTrabajo.EN_EJECUCION, EstadoTrabajo.TERMINADO}


def puede_crear_trabajo(user):
    """Diego, Rodrigo — 'trabajos: crear a partir de presupuesto aceptado'."""
    return user.has_perm("jobs.add_trabajo")


def puede_cambiar_estado_trabajo(user, trabajo, nuevo_estado):
    """
    Diego (change_trabajo) puede cualquier transición. Contri
    (manage_preparacion) solo puede llevar el trabajo a Preparando
    materiales o Listo. El técnico asignado (manage_ejecucion_propia)
    solo puede llevar SU PROPIO trabajo a En ejecución o Terminado —
    nunca el de otro técnico.
    """
    if user.has_perm("jobs.change_trabajo"):
        return True
    if nuevo_estado in ESTADOS_PREPARACION:
        return user.has_perm("jobs.manage_preparacion")
    if nuevo_estado in ESTADOS_EJECUCION:
        return (
            user.has_perm("jobs.manage_ejecucion_propia")
            and trabajo.tecnico_asignado_id == user.id
        )
    return False


def queryset_trabajos_visibles(user, queryset):
    """
    Diego/Rodrigo/Contri (roles de coordinación) ven todos los
    trabajos. Andrés (solo manage_ejecucion_propia, sin ningún permiso
    de coordinación) ve únicamente los suyos asignados — mismo
    criterio que Tarea en la Etapa 6.
    """
    if (
        user.has_perm("jobs.change_trabajo")
        or user.has_perm("jobs.add_trabajo")
        or user.has_perm("jobs.manage_preparacion")
    ):
        return queryset
    return queryset.filter(tecnico_asignado=user)
