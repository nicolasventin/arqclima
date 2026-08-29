from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import log_action
from apps.pricing.services import registrar_costo
from apps.stock.models import TipoMovimiento
from apps.stock.services import registrar_movimiento

from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra, TRANSICIONES_VALIDAS
from .permissions import (
    puede_aprobar_orden,
    puede_cancelar_orden,
    puede_cerrar_orden,
    puede_gestionar_orden,
)


class TransicionInvalidaError(ValueError):
    pass


@transaction.atomic
def crear_orden(proveedor, deposito_destino, usuario, notas=""):
    orden = OrdenDeCompra.objects.create(
        proveedor=proveedor,
        deposito_destino=deposito_destino,
        creado_por=usuario,
        notas=notas,
    )
    log_action(usuario, "crear_orden_compra", orden, detail=f"Orden creada para {proveedor}")
    return orden


def cantidad_recibida(linea):
    """Cantidad ya recibida para una línea, derivada del ledger de stock."""
    return linea.movimientos_stock.aggregate(total=Sum("cantidad"))["total"] or Decimal("0")


def cantidad_pendiente_recepcion(linea):
    return linea.cantidad - cantidad_recibida(linea)


def orden_tiene_recepciones(orden):
    return orden.movimientos_stock.filter(
        tipo=TipoMovimiento.ENTRADA,
        linea_orden_compra__isnull=False,
        cantidad__gt=0,
    ).exists()


def lineas_pendientes_recepcion(orden):
    lineas = list(
        orden.lineas.select_related("producto_proveedor__producto").order_by("pk")
    )
    return [
        linea
        for linea in lineas
        if cantidad_pendiente_recepcion(linea) > 0
    ]


def orden_completamente_recibida(orden):
    lineas = list(orden.lineas.all().order_by("pk"))
    if not lineas:
        return False
    return all(cantidad_pendiente_recepcion(linea) <= 0 for linea in lineas)


def _validar_permiso_transicion(nuevo_estado, usuario):
    if nuevo_estado in (EstadoOrdenCompra.APROBADA, EstadoOrdenCompra.RECHAZADA):
        if not puede_aprobar_orden(usuario):
            raise PermissionError("No tiene permiso para aprobar o rechazar órdenes de compra.")
        return

    if nuevo_estado == EstadoOrdenCompra.CANCELADA:
        if not puede_cancelar_orden(usuario):
            raise PermissionError("No tiene permiso para cancelar órdenes de compra.")
        return

    if not puede_gestionar_orden(usuario):
        raise PermissionError("No tiene permiso para gestionar órdenes de compra.")


@transaction.atomic
def cambiar_estado_orden(orden, nuevo_estado, usuario, detalle="", motivo=""):
    """
    Transiciones manuales del ciclo de compra.

    Las transiciones derivadas de recepción (Recepción parcial / Recibida)
    y el cierre administrativo tienen servicios dedicados para que no se
    puedan producir estados inconsistentes sin una recepción real.
    """
    if nuevo_estado in (
        EstadoOrdenCompra.RECEPCION_PARCIAL,
        EstadoOrdenCompra.RECIBIDA,
        EstadoOrdenCompra.CERRADA,
    ):
        raise TransicionInvalidaError(
            "Ese estado se gestiona desde la recepción/cierre de la orden, no manualmente."
        )

    _validar_permiso_transicion(nuevo_estado, usuario)

    orden_bloqueada = OrdenDeCompra.objects.select_for_update().get(pk=orden.pk)
    estado_anterior = orden_bloqueada.estado
    permitidos = TRANSICIONES_VALIDAS.get(estado_anterior, set())
    if nuevo_estado not in permitidos:
        raise TransicionInvalidaError(
            f"No se puede pasar de '{estado_anterior}' a '{nuevo_estado}'."
        )

    motivo_limpio = (motivo or "").strip()
    ahora = timezone.now()
    update_fields = ["estado"]

    if nuevo_estado == EstadoOrdenCompra.PENDIENTE_APROBACION:
        if not orden_bloqueada.lineas.exists():
            raise ValueError("No se puede enviar a aprobación una orden sin líneas.")
        orden_bloqueada.solicitud_aprobacion_por = usuario
        orden_bloqueada.solicitud_aprobacion_en = ahora
        update_fields += ["solicitud_aprobacion_por", "solicitud_aprobacion_en"]

    elif nuevo_estado == EstadoOrdenCompra.APROBADA:
        if not orden_bloqueada.lineas.exists():
            raise ValueError("No se puede aprobar una orden sin líneas.")
        orden_bloqueada.aprobada_por = usuario
        orden_bloqueada.aprobada_en = ahora
        update_fields += ["aprobada_por", "aprobada_en"]

    elif nuevo_estado == EstadoOrdenCompra.RECHAZADA:
        if not motivo_limpio:
            raise ValueError("Debe indicar el motivo del rechazo.")
        orden_bloqueada.rechazada_por = usuario
        orden_bloqueada.rechazada_en = ahora
        orden_bloqueada.motivo_rechazo = motivo_limpio
        update_fields += ["rechazada_por", "rechazada_en", "motivo_rechazo"]

    elif nuevo_estado == EstadoOrdenCompra.ENVIADA:
        orden_bloqueada.enviada_por = usuario
        orden_bloqueada.enviada_en = ahora
        update_fields += ["enviada_por", "enviada_en"]

    elif nuevo_estado == EstadoOrdenCompra.CANCELADA:
        if orden_tiene_recepciones(orden_bloqueada):
            raise ValueError(
                "Una orden que ya recibió mercadería no se cancela; debe cerrarse."
            )
        if not motivo_limpio:
            raise ValueError("Debe indicar el motivo de la cancelación.")
        orden_bloqueada.cancelada_por = usuario
        orden_bloqueada.cancelada_en = ahora
        orden_bloqueada.motivo_cancelacion = motivo_limpio
        update_fields += ["cancelada_por", "cancelada_en", "motivo_cancelacion"]

    orden_bloqueada.estado = nuevo_estado
    orden_bloqueada.save(update_fields=update_fields)

    accion_por_estado = {
        EstadoOrdenCompra.PENDIENTE_APROBACION: "solicitar_aprobacion_orden_compra",
        EstadoOrdenCompra.APROBADA: "aprobar_orden_compra",
        EstadoOrdenCompra.RECHAZADA: "rechazar_orden_compra",
        EstadoOrdenCompra.BORRADOR: "reabrir_orden_compra",
        EstadoOrdenCompra.ENVIADA: "enviar_orden_compra_proveedor",
        EstadoOrdenCompra.CANCELADA: "cancelar_orden_compra",
    }
    texto_detalle = detalle or f"{estado_anterior} → {nuevo_estado}"
    if motivo_limpio:
        texto_detalle = f"{texto_detalle}. Motivo: {motivo_limpio}"

    log_action(
        usuario,
        accion_por_estado.get(nuevo_estado, "cambiar_estado_orden_compra"),
        orden_bloqueada,
        detail=texto_detalle,
    )

    orden.estado = nuevo_estado
    for campo in (
        "solicitud_aprobacion_por",
        "solicitud_aprobacion_en",
        "aprobada_por",
        "aprobada_en",
        "rechazada_por",
        "rechazada_en",
        "motivo_rechazo",
        "enviada_por",
        "enviada_en",
        "cancelada_por",
        "cancelada_en",
        "motivo_cancelacion",
    ):
        setattr(orden, campo, getattr(orden_bloqueada, campo))
    return orden_bloqueada


@transaction.atomic
def recibir_linea(linea, cantidad, costo_real, usuario):
    """
    Registra una recepción física.

    10E endurece la semántica: una orden debe haber sido marcada Enviada
    antes de recibir. La primera recepción mueve automáticamente a
    Recepción parcial, y la última unidad pendiente de todas las líneas
    mueve automáticamente a Recibida.
    """
    if cantidad <= 0:
        raise ValueError("La cantidad recibida tiene que ser mayor a cero.")

    orden_bloqueada = OrdenDeCompra.objects.select_for_update().get(pk=linea.orden_id)
    linea_bloqueada = (
        LineaOrdenCompra.objects.select_for_update()
        .select_related("producto_proveedor__producto")
        .get(pk=linea.pk)
    )

    if orden_bloqueada.estado not in (
        EstadoOrdenCompra.ENVIADA,
        EstadoOrdenCompra.RECEPCION_PARCIAL,
    ):
        raise ValueError(
            "Solo se puede recibir mercadería de una orden Enviada o con Recepción parcial."
        )

    pendiente = cantidad_pendiente_recepcion(linea_bloqueada)
    if cantidad > pendiente:
        raise ValueError(f"No puede superar lo pendiente ({pendiente}).")

    registrar_costo(
        linea_bloqueada.producto_proveedor,
        costo_real,
        usuario,
        origen="orden_compra",
    )
    movimiento = registrar_movimiento(
        producto=linea_bloqueada.producto_proveedor.producto,
        deposito=orden_bloqueada.deposito_destino,
        tipo=TipoMovimiento.ENTRADA,
        cantidad=cantidad,
        usuario=usuario,
        orden_compra=orden_bloqueada,
        linea_orden_compra=linea_bloqueada,
        referencia_libre=f"Recepción de {orden_bloqueada}",
    )

    ahora = timezone.now()
    update_fields = ["estado"]
    if orden_bloqueada.primera_recepcion_en is None:
        orden_bloqueada.primera_recepcion_en = ahora
        update_fields.append("primera_recepcion_en")

    if orden_completamente_recibida(orden_bloqueada):
        orden_bloqueada.estado = EstadoOrdenCompra.RECIBIDA
        orden_bloqueada.recibida_en = ahora
        update_fields.append("recibida_en")
        accion = "recibir_orden_compra_completa"
        detalle = f"Recepción completa. Último movimiento: {cantidad}."
    else:
        orden_bloqueada.estado = EstadoOrdenCompra.RECEPCION_PARCIAL
        accion = "recibir_orden_compra_parcial"
        detalle = f"Recepción parcial de {cantidad} en línea #{linea_bloqueada.pk}."

    orden_bloqueada.save(update_fields=update_fields)
    log_action(usuario, accion, orden_bloqueada, detail=detalle)

    linea.orden.estado = orden_bloqueada.estado
    return movimiento


@transaction.atomic
def cerrar_orden(orden, usuario, motivo=""):
    """
    Cierra administrativamente una orden recibida.

    Si todavía hay líneas pendientes, el cierre significa que ese
    remanente ya no se espera del proveedor y por eso exige motivo.
    """
    if not puede_cerrar_orden(usuario):
        raise PermissionError("No tiene permiso para cerrar órdenes de compra.")

    orden_bloqueada = OrdenDeCompra.objects.select_for_update().get(pk=orden.pk)
    if orden_bloqueada.estado not in (
        EstadoOrdenCompra.RECEPCION_PARCIAL,
        EstadoOrdenCompra.RECIBIDA,
    ):
        raise TransicionInvalidaError(
            "Solo se puede cerrar una orden con recepción parcial o completamente recibida."
        )

    pendientes = lineas_pendientes_recepcion(orden_bloqueada)
    motivo_limpio = (motivo or "").strip()
    if pendientes and not motivo_limpio:
        raise ValueError(
            "Debe indicar por qué se cierra la orden con mercadería pendiente."
        )

    estado_anterior = orden_bloqueada.estado
    orden_bloqueada.estado = EstadoOrdenCompra.CERRADA
    orden_bloqueada.cerrada_por = usuario
    orden_bloqueada.cerrada_en = timezone.now()
    orden_bloqueada.motivo_cierre = motivo_limpio
    orden_bloqueada.save(
        update_fields=["estado", "cerrada_por", "cerrada_en", "motivo_cierre"]
    )

    detalle = f"{estado_anterior} → cerrada"
    if pendientes:
        detalle += f". Líneas pendientes abandonadas: {len(pendientes)}."
    if motivo_limpio:
        detalle += f" Motivo: {motivo_limpio}"

    log_action(usuario, "cerrar_orden_compra", orden_bloqueada, detail=detalle)

    orden.estado = EstadoOrdenCompra.CERRADA
    orden.cerrada_por = usuario
    orden.cerrada_en = orden_bloqueada.cerrada_en
    orden.motivo_cierre = motivo_limpio
    return orden_bloqueada
