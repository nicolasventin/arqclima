from apps.audit.services import log_action
from apps.quotes.models import EstadoPresupuesto

from .models import ORDEN_ESTADOS, Trabajo


class TransicionInvalidaError(ValueError):
    pass


def crear_trabajo(presupuesto, usuario, tecnico_asignado=None):
    """
    Único punto de entrada para que nazca un Trabajo. No es un efecto
    automático de aceptar el presupuesto — alguien con permiso dispara
    esta acción a propósito, después de que el presupuesto ya está
    Aceptado.
    """
    if presupuesto.estado != EstadoPresupuesto.ACEPTADO:
        raise ValueError("Solo se puede crear un Trabajo a partir de un Presupuesto Aceptado.")
    if hasattr(presupuesto, "trabajo"):
        raise ValueError("Este presupuesto ya tiene un Trabajo creado.")

    trabajo = Trabajo.objects.create(
        presupuesto=presupuesto,
        tecnico_asignado=tecnico_asignado,
        direccion=presupuesto.direccion,
        observaciones=presupuesto.notas_generales,
        creado_por=usuario,
    )
    log_action(usuario, "crear_trabajo", trabajo, detail=f"Trabajo creado desde {presupuesto}")
    return trabajo


def cambiar_estado_trabajo(trabajo, nuevo_estado, usuario, detalle=""):
    """
    El estado de un Trabajo solo avanza (regla de negocio 10) — se
    puede saltear etapas (ej. Pendiente→Listo directo si ya había
    stock disponible), pero nunca retroceder. Se valida comparando la
    posición en ORDEN_ESTADOS, no con un grafo de pares explícito.
    """
    try:
        idx_actual = ORDEN_ESTADOS.index(trabajo.estado)
        idx_nuevo = ORDEN_ESTADOS.index(nuevo_estado)
    except ValueError:
        raise TransicionInvalidaError(f"Estado desconocido: '{nuevo_estado}'.")

    if idx_nuevo <= idx_actual:
        raise TransicionInvalidaError(
            f"No se puede pasar de '{trabajo.estado}' a '{nuevo_estado}': "
            "el estado de un Trabajo solo avanza, nunca retrocede."
        )

    estado_anterior = trabajo.estado
    trabajo.estado = nuevo_estado
    trabajo.save(update_fields=["estado"])
    log_action(
        usuario, "cambiar_estado_trabajo", trabajo, detail=detalle or f"{estado_anterior} → {nuevo_estado}"
    )
    return trabajo
