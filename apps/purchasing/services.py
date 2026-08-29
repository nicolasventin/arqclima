from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.audit.services import log_action
from apps.pricing.services import registrar_costo
from apps.stock.models import TipoMovimiento
from apps.stock.services import registrar_movimiento

from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra, TRANSICIONES_VALIDAS


class TransicionInvalidaError(ValueError):
    pass


@transaction.atomic
def crear_orden(proveedor, deposito_destino, usuario, notas=""):
    orden = OrdenDeCompra.objects.create(
        proveedor=proveedor, deposito_destino=deposito_destino, creado_por=usuario, notas=notas
    )
    log_action(usuario, "crear_orden_compra", orden, detail=f"Orden creada para {proveedor}")
    return orden


@transaction.atomic
def cambiar_estado_orden(orden, nuevo_estado, usuario, detalle=""):
    """
    Único punto de entrada para mover el estado — no valida permisos
    (misma separación de responsabilidades que Presupuesto/Trabajo):
    quién puede pedir qué transición se resuelve en
    apps.purchasing.permissions, llamado desde la vista.

    Se vuelve a leer y bloquear la orden dentro de la transacción para
    no validar contra un objeto viejo recibido por la vista.
    """
    orden_bloqueada = OrdenDeCompra.objects.select_for_update().get(pk=orden.pk)
    permitidos = TRANSICIONES_VALIDAS.get(orden_bloqueada.estado, set())
    if nuevo_estado not in permitidos:
        raise TransicionInvalidaError(
            f"No se puede pasar de '{orden_bloqueada.estado}' a '{nuevo_estado}'."
        )

    estado_anterior = orden_bloqueada.estado
    orden_bloqueada.estado = nuevo_estado
    orden_bloqueada.save(update_fields=["estado"])
    log_action(
        usuario, "cambiar_estado_orden_compra", orden_bloqueada,
        detail=detalle or f"{estado_anterior} → {nuevo_estado}",
    )

    orden.estado = nuevo_estado
    return orden_bloqueada


def cantidad_recibida(linea):
    """Se deriva sumando el ledger de MovimientoStock vinculado a esta línea (permite recepciones parciales)."""
    return linea.movimientos_stock.aggregate(total=Sum("cantidad"))["total"] or Decimal("0")


def cantidad_pendiente_recepcion(linea):
    return linea.cantidad - cantidad_recibida(linea)


@transaction.atomic
def recibir_linea(linea, cantidad, costo_real, usuario):
    """
    Acción explícita y separada — no automática al aprobar/enviar
    (mismo criterio que enviar_material()/generar_listado_materiales()
    en jobs): alguien la dispara cuando la mercadería físicamente
    llega, potencialmente días después de aprobar la orden.

    La orden se bloquea primero y la línea después. Ese orden de locks
    es estable para evitar deadlocks con una transición concurrente de
    la orden. La cantidad pendiente se recalcula recién después del
    lock, por lo que dos recepciones simultáneas no pueden sobrepasar
    la cantidad comprada.

    costo_real es editable respecto al costo_esperado cargado al armar
    la línea (la factura real del proveedor puede diferir) — se
    registra en el historial de costos tal cual se confirma acá, no el
    costo_esperado original.
    """
    if cantidad <= 0:
        raise ValueError("La cantidad recibida tiene que ser mayor a cero.")

    orden_bloqueada = OrdenDeCompra.objects.select_for_update().get(pk=linea.orden_id)
    linea_bloqueada = (
        LineaOrdenCompra.objects.select_for_update()
        .select_related("producto_proveedor__producto")
        .get(pk=linea.pk)
    )

    if orden_bloqueada.estado not in (EstadoOrdenCompra.APROBADA, EstadoOrdenCompra.ENVIADA):
        raise ValueError("Solo se puede recibir mercadería de una orden Aprobada o Enviada.")

    pendiente = cantidad_pendiente_recepcion(linea_bloqueada)
    if cantidad > pendiente:
        raise ValueError(f"No puede superar lo pendiente ({pendiente}).")

    registrar_costo(
        linea_bloqueada.producto_proveedor,
        costo_real,
        usuario,
        origen="orden_compra",
    )
    return registrar_movimiento(
        producto=linea_bloqueada.producto_proveedor.producto,
        deposito=orden_bloqueada.deposito_destino,
        tipo=TipoMovimiento.ENTRADA,
        cantidad=cantidad,
        usuario=usuario,
        orden_compra=orden_bloqueada,
        linea_orden_compra=linea_bloqueada,
        referencia_libre=f"Recepción de {orden_bloqueada}",
    )
