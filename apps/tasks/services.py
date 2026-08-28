from django.utils import timezone

from apps.audit.services import log_action

from .models import EstadoTarea, Tarea


class TransicionInvalidaError(ValueError):
    pass


TRANSICIONES_VALIDAS = {
    EstadoTarea.PENDIENTE: {EstadoTarea.EN_PROCESO, EstadoTarea.COMPLETADA},
    EstadoTarea.EN_PROCESO: {EstadoTarea.COMPLETADA},
    EstadoTarea.COMPLETADA: set(),
}


def cambiar_estado_tarea(tarea, nuevo_estado, usuario):
    """
    Único punto de entrada para mover el estado de una tarea. Permite
    saltar Pendiente→Completada directo (una tarea de dos minutos no
    debería obligar a pasar por "En proceso"). Completada es terminal:
    si se cerró por error, se crea una tarea nueva, no se reabre —
    mismo criterio que Presupuesto en la Etapa 5.
    """
    estado_actual = tarea.estado
    permitidos = TRANSICIONES_VALIDAS.get(estado_actual, set())
    if nuevo_estado not in permitidos:
        raise TransicionInvalidaError(f"No se puede pasar de '{estado_actual}' a '{nuevo_estado}'.")

    tarea.estado = nuevo_estado
    if nuevo_estado == EstadoTarea.COMPLETADA:
        tarea.completada_en = timezone.now()
        tarea.save(update_fields=["estado", "completada_en"])
    else:
        tarea.save(update_fields=["estado"])

    log_action(
        usuario, "cambiar_estado_tarea", tarea, detail=f"{estado_actual} → {nuevo_estado}"
    )
    return tarea


def crear_tarea_automatica(tipo, titulo, descripcion, asignado_a, presupuesto=None, producto=None, deposito=""):
    """
    Único punto de entrada para que una de las 3 reglas automáticas de
    la Etapa 9 (regla de negocio 17) cree una Tarea. Deliberadamente NO
    decide acá si corresponde crearla o no: el criterio de idempotencia
    es distinto para cada una de las 3 reglas (posterior al último
    envío para seguimiento, una sola vez por presupuesto para el aviso
    de vencimiento, mientras no se resuelva para stock mínimo) — vive
    en el management command de cada regla, mismo criterio que
    vencer_presupuestos (la condición de "a quién le toca" vive en el
    comando, no en un service genérico que la esconda).

    asignado_por queda en None a propósito: no la asignó una persona.
    log_action(usuario=None, ...) reusa la convención que AuditLog ya
    tenía desde la Etapa 1 (su __str__ ya decía `usuario or "Sistema"`,
    nunca se había usado hasta ahora).
    """
    tarea = Tarea.objects.create(
        titulo=titulo,
        descripcion=descripcion,
        asignado_a=asignado_a,
        asignado_por=None,
        generada_por=tipo,
        presupuesto=presupuesto,
        producto=producto,
        deposito=deposito,
    )
    log_action(None, "generar_tarea_automatica", tarea, detail=f"{tipo}: {titulo}")
    return tarea
