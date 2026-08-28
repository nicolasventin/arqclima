from decimal import Decimal

from django.db.models import Sum

from apps.audit.services import log_action
from apps.quotes.models import EstadoPresupuesto
from apps.stock.models import Deposito, TipoMovimiento
from apps.stock.services import registrar_movimiento

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

    accion = "cambiar_estado_trabajo"
    direccion = "avanzado" if idx_nuevo > idx_actual else "retrocedido"

    # Regla de negocio 6/Etapa 7 aplicada acá: pasar a Listo con
    # material sin enviar NO bloquea (Contri puede tener razones para
    # marcarlo igual), pero queda auditado con el detalle de qué
    # faltaba en ESE momento — mismo criterio que el margen bajo en
    # enviar_presupuesto(). No se sobreescribe si el caller ya mandó un
    # detalle propio.
    if nuevo_estado == EstadoTrabajo.LISTO and not detalle:
        pendientes = materiales_pendientes_de_envio(trabajo)
        if pendientes:
            accion = "trabajo_marcado_listo_con_pendientes"
            detalle = "Marcado Listo con material pendiente de envío: " + "; ".join(
                f"{m} (faltan {cantidad_pendiente_envio(m)})" for m in pendientes
            )

    log_action(
        usuario,
        accion,
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

    ItemPresupuesto.cantidad es la cantidad para UNA unidad (regla de
    negocio 9: "cantidad de unidades" multiplica a nivel de todo el
    presupuesto, no ítem por ítem — mismo criterio que calcular_totales()
    en quotes/services.py). Un presupuesto de "3 casas" con 2 termostatos
    por casa necesita 6 termostatos en total para preparar la obra, no 2
    — por eso acá SÍ hay que multiplicar por cantidad_unidades, a
    diferencia de ItemPresupuesto donde el campo se deja sin tocar y el
    factor se aplica solo una vez al calcular el total en dinero.
    """
    if trabajo.materiales.exists() or trabajo.etapas.exists():
        raise ValueError("Este trabajo ya tiene un listado de materiales generado.")

    mapa_etapas = {
        seccion.pk: EtapaTrabajo.objects.create(
            trabajo=trabajo, titulo=seccion.titulo, seccion_origen=seccion, orden=seccion.orden
        )
        for seccion in trabajo.presupuesto.secciones.all()
    }

    cantidad_unidades = trabajo.presupuesto.cantidad_unidades
    items = trabajo.presupuesto.items.filter(producto__isnull=False, incluido=True)
    for item in items:
        MaterialTrabajo.objects.create(
            trabajo=trabajo,
            etapa=mapa_etapas.get(item.seccion_id),
            producto=item.producto,
            item_presupuesto_origen=item,
            cantidad_necesaria=item.cantidad * cantidad_unidades,
            orden=item.orden,
        )

    log_action(
        usuario, "generar_listado_materiales", trabajo,
        detail=f"{items.count()} material(es) generados desde {trabajo.presupuesto}",
    )
    return trabajo


# --- Parte 3: envío y consumo real de materiales (regla de negocio 11) ---
#
# Nada de esto se persiste como campo mutable en MaterialTrabajo — se
# deriva sumando el ledger de MovimientoStock vinculado a cada línea
# (mismo criterio que stock_actual() y cantidad_pendiente_devolucion()
# en la Etapa 7). Los materiales SIN producto de catálogo (texto
# libre) no tienen conexión con Stock: quedan en 0 para todo esto.


def cantidad_enviada(material):
    if material.producto_id is None:
        return Decimal("0")
    total = material.movimientos_stock.filter(tipo=TipoMovimiento.SALIDA).aggregate(
        total=Sum("cantidad")
    )["total"] or Decimal("0")
    return abs(total)


def cantidad_devuelta(material):
    if material.producto_id is None:
        return Decimal("0")
    return material.movimientos_stock.filter(tipo=TipoMovimiento.ENTRADA).aggregate(
        total=Sum("cantidad")
    )["total"] or Decimal("0")


def cantidad_pendiente_envio(material):
    """Cuánto falta enviar del plan (cantidad_necesaria) — 0 si ya se mandó todo o si es manual."""
    if material.producto_id is None:
        return Decimal("0")
    return material.cantidad_necesaria - cantidad_enviada(material)


def cantidad_usada_neta(material):
    """Lo enviado menos lo que ya volvió como sobrante — lo que se asume efectivamente consumido."""
    return cantidad_enviada(material) - cantidad_devuelta(material)


def materiales_pendientes_de_envio(trabajo):
    return [
        material
        for material in trabajo.materiales.filter(producto__isnull=False)
        if cantidad_pendiente_envio(material) > 0
    ]


def enviar_material(material, usuario):
    """
    Regla de negocio 11: "se asume que se usó todo" — manda exactamente
    lo que falta del plan (cantidad_necesaria menos lo ya enviado
    antes), no pide una cantidad. Si cantidad_necesaria se edita hacia
    arriba después de un envío, un nuevo envío manda solo el delta.
    """
    if material.producto_id is None:
        raise ValueError("Este material no tiene producto de catálogo — no se puede enviar desde Stock.")

    pendiente = cantidad_pendiente_envio(material)
    if pendiente <= 0:
        raise ValueError("Este material ya fue enviado por completo.")

    return registrar_movimiento(
        producto=material.producto,
        deposito=Deposito.GENERAL,
        tipo=TipoMovimiento.SALIDA,
        cantidad=-pendiente,
        usuario=usuario,
        trabajo=material.trabajo,
        material_trabajo=material,
        referencia_libre=f"Envío a {material.trabajo}",
    )


def enviar_materiales_pendientes(trabajo, usuario):
    """Envía en bloque todos los materiales de catálogo con algo pendiente."""
    return [
        enviar_material(material, usuario) for material in materiales_pendientes_de_envio(trabajo)
    ]


def registrar_sobrante(material, cantidad_sobrante, usuario):
    """
    Regla de negocio 11: el sobrante vuelve a stock. Es una ENTRADA
    simple a stock general — no se reusa el tipo Devolución, que en la
    Etapa 7 tiene un significado específico y distinto (atado a
    requiere_devolucion/salida_relacionada, exclusivo del circuito de
    repuestos de Gabriel, que no pasa por Trabajo).
    """
    if material.producto_id is None:
        raise ValueError("Este material no tiene producto de catálogo — no se puede devolver a Stock.")
    if cantidad_sobrante <= 0:
        raise ValueError("La cantidad de sobrante tiene que ser mayor a cero.")

    maximo = cantidad_usada_neta(material)
    if cantidad_sobrante > maximo:
        raise ValueError(f"No puede superar lo enviado y no devuelto todavía ({maximo}).")

    return registrar_movimiento(
        producto=material.producto,
        deposito=Deposito.GENERAL,
        tipo=TipoMovimiento.ENTRADA,
        cantidad=cantidad_sobrante,
        usuario=usuario,
        trabajo=material.trabajo,
        material_trabajo=material,
        referencia_libre=f"Sobrante devuelto de {material.trabajo}",
    )
