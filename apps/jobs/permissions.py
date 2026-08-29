from .models import EstadoTrabajo

ESTADOS_PREPARACION = {EstadoTrabajo.PREPARANDO_MATERIALES, EstadoTrabajo.LISTO}
ESTADOS_EJECUCION = {EstadoTrabajo.EN_EJECUCION}


def puede_crear_trabajo(user):
    """Diego, Rodrigo — 'trabajos: crear a partir de presupuesto aceptado'."""
    return user.has_perm("jobs.add_trabajo")


def puede_asignar_tecnico(user):
    """
    Asignar/reasignar el técnico de un trabajo (coordinación de obra)
    es EXCLUSIVO de Diego según la matriz de roles original — a
    diferencia de crear el trabajo, que Rodrigo también puede hacer.
    Un trabajo creado por Rodrigo nace sin técnico asignado; Diego lo
    asigna después.
    """
    return user.has_perm("jobs.change_trabajo")


def puede_cancelar_trabajo(user):
    """
    Cancelar un trabajo en curso es una decisión de negocio
    significativa (mismo peso que revert_presupuesto_aceptado en
    quotes) — exclusiva de Diego.
    """
    return user.has_perm("jobs.change_trabajo")


def puede_gestionar_materiales(user):
    """
    Generar el listado inicial y editar materiales/etapas después es
    del mismo dominio que preparar el trabajo (Contri) — reusa
    manage_preparacion en vez de un permiso nuevo, porque son
    literalmente la misma responsabilidad: Contri "ve trabajos para
    saber qué preparar" y actúa sobre ESTE listado para hacerlo.
    """
    return user.has_perm("jobs.change_trabajo") or user.has_perm("jobs.manage_preparacion")


def puede_registrar_consumo_material(user, material):
    """
    Regla de negocio 11: "el técnico solo edita el número si sobró
    algo" — el técnico asignado corrige el consumo de SU PROPIO
    trabajo, mismo alcance que manage_ejecucion_propia (Parte 1). No es
    un permiso nuevo: es la misma responsabilidad de ejecución de obra.
    """
    if material.trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        return False
    if user.has_perm("jobs.change_trabajo"):
        return True
    return (
        user.has_perm("jobs.manage_ejecucion_propia")
        and material.trabajo.tecnico_asignado_id == user.id
    )


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
    if trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        return False
    if nuevo_estado == EstadoTrabajo.TERMINADO:
        return False
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



def puede_finalizar_trabajo(user, trabajo):
    """
    Terminar un trabajo deja de ser una transición genérica en 10F.
    Diego puede finalizar cualquiera; el técnico de campo solo el suyo.
    """
    if user.has_perm("jobs.change_trabajo"):
        return True
    return (
        user.has_perm("jobs.manage_ejecucion_propia")
        and trabajo.tecnico_asignado_id == user.id
    )


def trabajo_esta_cerrado(trabajo):
    return trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO)
