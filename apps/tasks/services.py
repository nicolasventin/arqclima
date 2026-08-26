from django.utils import timezone

from apps.audit.services import log_action

from .models import EstadoTarea


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
