from .models import EstadoTrabajo

ESTADOS_PREPARACION = {EstadoTrabajo.PREPARANDO_MATERIALES, EstadoTrabajo.LISTO}
ESTADOS_EJECUCION = {EstadoTrabajo.EN_EJECUCION, EstadoTrabajo.TERMINADO}


def puede_crear_trabajo(user):
    """Diego, Rodrigo — 'trabajos: crear a partir de presupuesto aceptado'."""
    return user.has_perm("jobs.add_trabajo")


def puede_cambiar_estado_trabajo(user, trabajo, nuevo_estado):
    """
    Gobierna tanto avanzar como retroceder: no distingue dirección,
    solo evalúa si el usuario tiene autoridad sobre el ESTADO DESTINO.
    Así, quien puede llevar el trabajo a un estado hacia adelante
    también puede corregirlo de vuelta a ese mismo estado si se cargó
    mal (ej. Contri deshace un "Listo" marcado por error, volviendo a
    "Preparando materiales" — ambos son su propio rango).

    Diego (change_trabajo) puede cualquier transición, en cualquier
    dirección, incluido volver hasta Pendiente (que no es el destino
    de nadie más). Contri (manage_preparacion) puede ir hacia
    Preparando materiales o Listo. El técnico asignado
    (manage_ejecucion_propia) puede ir hacia En ejecución o Terminado,
    pero solo en SU PROPIO trabajo — nunca el de otro técnico, y no
    puede retroceder más allá de su propio rango (ej. no puede volver
    a "Preparando materiales", que es territorio de Contri).
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
