from apps.audit.services import log_action
from apps.quotes.models import EstadoPresupuesto

from .models import ORDEN_ESTADOS, EstadoTrabajo, EtapaTrabajo, MaterialTrabajo, Trabajo


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
    except ValueError:
        raise TransicionInvalidaError(
            f"El trabajo está '{trabajo.estado}': no forma parte de la secuencia de avance "
            "(un Cancelado no se reabre)."
        )
    try:
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


def cancelar_trabajo(trabajo, usuario, motivo=""):
    """
    Cancelado es una salida terminal APARTE de ORDEN_ESTADOS (no
    "avanza" ni "retrocede" — ver el comentario en models.py). Se
    permite desde cualquier estado no resuelto: un trabajo Terminado
    ya está resuelto, y uno ya Cancelado no se cancela de nuevo. No
    hay reapertura: si el trabajo cancelado necesita retomarse, es una
    decisión nueva de negocio, no una transición de estado.
    """
    if trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise TransicionInvalidaError(f"No se puede cancelar un trabajo en estado '{trabajo.estado}'.")

    estado_anterior = trabajo.estado
    trabajo.estado = EstadoTrabajo.CANCELADO
    trabajo.save(update_fields=["estado"])
    log_action(
        usuario,
        "cancelar_trabajo",
        trabajo,
        detail=motivo or f"Cancelado desde '{estado_anterior}'",
    )
    return trabajo


def generar_listado_materiales(trabajo, usuario):
    """
    Acción explícita y única (no automática al crear el trabajo, mismo
    criterio que crear_trabajo() en sí) — no es una resincronización:
    bloquea si el trabajo ya tiene materiales o etapas cargadas. A
    partir de la carga inicial, el listado se edita a mano.

    Crea una EtapaTrabajo por cada SeccionPresupuesto del presupuesto
    de origen (en orden), y un MaterialTrabajo por cada ItemPresupuesto
    con producto de catálogo e incluido=True (los conceptos manuales
    tipo mano de obra no son "material" y se excluyen).
    """
    if trabajo.materiales.exists() or trabajo.etapas.exists():
        raise ValueError("Este trabajo ya tiene un listado de materiales generado.")

    mapa_etapas = {
        seccion.pk: EtapaTrabajo.objects.create(
            trabajo=trabajo, titulo=seccion.titulo, seccion_origen=seccion, orden=seccion.orden
        )
        for seccion in trabajo.presupuesto.secciones.all()
    }

    items = trabajo.presupuesto.items.filter(producto__isnull=False, incluido=True)
    for item in items:
        MaterialTrabajo.objects.create(
            trabajo=trabajo,
            etapa=mapa_etapas.get(item.seccion_id),
            producto=item.producto,
            item_presupuesto_origen=item,
            cantidad_necesaria=item.cantidad,
            orden=item.orden,
        )

    log_action(
        usuario, "generar_listado_materiales", trabajo,
        detail=f"{items.count()} material(es) generados desde {trabajo.presupuesto}",
    )
    return trabajo
