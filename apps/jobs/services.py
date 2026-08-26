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
    El estado de un Trabajo normalmente avanza —y se puede saltear
    etapas (ej. Pendiente→Listo directo)— pero TAMBIÉN se puede
    retroceder para corregir un error de carga (ej. Contri marcó
    "Listo" sin que estuviera completo el material, o Andrés avanzó a
    "En ejecución" antes de tiempo). Mismo criterio que Presupuesto,
    que también tiene transiciones explícitas de vuelta atrás
    (Enviado→Borrador, Rechazado→Borrador): no hay razón de negocio
    para que Trabajo sea estrictamente irreversible, y menos acá,
    donde presupuesto.trabajo es OneToOne — si un estado avanzado por
    error quedara sin forma de corregirse, no habría ni siquiera el
    workaround de "crear uno nuevo" que tiene Tarea.

    Esta función NO valida permisos — es la misma separación de
    responsabilidades que Presupuesto.cambiar_estado(): quién puede
    pedir qué transición (en cualquier dirección) se resuelve en
    apps.jobs.permissions.puede_cambiar_estado_trabajo(), llamado
    desde la vista.
    """
    try:
        idx_actual = ORDEN_ESTADOS.index(trabajo.estado)
        idx_nuevo = ORDEN_ESTADOS.index(nuevo_estado)
    except ValueError:
        raise TransicionInvalidaError(f"Estado desconocido: '{nuevo_estado}'.")

    if idx_nuevo == idx_actual:
        raise TransicionInvalidaError(f"El trabajo ya está en estado '{trabajo.estado}'.")

    estado_anterior = trabajo.estado
    trabajo.estado = nuevo_estado
    trabajo.save(update_fields=["estado"])

    direccion = "avanzado" if idx_nuevo > idx_actual else "retrocedido"
    log_action(
        usuario,
        "cambiar_estado_trabajo",
        trabajo,
        detail=detalle or f"{direccion}: {estado_anterior} → {nuevo_estado}",
    )
    return trabajo
