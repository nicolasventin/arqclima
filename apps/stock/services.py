from decimal import Decimal

from django.db.models import Sum

from apps.audit.services import log_action

from .models import Deposito, MovimientoStock, TipoMovimiento

SIGNO_ESPERADO = {
    TipoMovimiento.ENTRADA: 1,
    TipoMovimiento.SALIDA: -1,
    TipoMovimiento.DEVOLUCION: 1,
    TipoMovimiento.AJUSTE: None,  # un ajuste puede corregir para arriba o para abajo
}


def stock_actual(producto, deposito):
    total = MovimientoStock.objects.filter(producto=producto, deposito=deposito).aggregate(
        total=Sum("cantidad")
    )["total"]
    return total or Decimal("0")


def registrar_movimiento(
    *,
    producto,
    deposito,
    tipo,
    cantidad,
    usuario,
    requiere_devolucion=False,
    salida_relacionada=None,
    referencia_libre="",
    trabajo=None,
    material_trabajo=None,
    orden_compra=None,
    linea_orden_compra=None,
    detalle="",
):
    """
    Único punto de entrada para crear un MovimientoStock — nunca se
    inserta directo con .create() fuera de acá, para que la validación
    de signo/tipo y la auditoría sean parejas en todos los flujos.

    `trabajo`/`material_trabajo`/`orden_compra`/`linea_orden_compra`
    (Etapa 8): se pasan en el mismo INSERT porque MovimientoStock es
    append-only — un UPDATE posterior para completarlos violaría el
    propio trigger de inmutabilidad.
    """
    signo_esperado = SIGNO_ESPERADO.get(tipo)
    if signo_esperado is not None and cantidad * signo_esperado <= 0:
        palabra = "positiva" if signo_esperado > 0 else "negativa"
        raise ValueError(f"La cantidad para un movimiento de tipo '{tipo}' debe ser {palabra}.")

    if requiere_devolucion and not (tipo == TipoMovimiento.SALIDA and deposito == Deposito.REPUESTOS):
        raise ValueError("requiere_devolucion solo aplica a una Salida de repuestos.")

    if salida_relacionada is not None and tipo != TipoMovimiento.DEVOLUCION:
        raise ValueError("salida_relacionada solo aplica a un movimiento de tipo Devolución.")

    movimiento = MovimientoStock.objects.create(
        producto=producto,
        deposito=deposito,
        tipo=tipo,
        cantidad=cantidad,
        requiere_devolucion=requiere_devolucion,
        salida_relacionada=salida_relacionada,
        referencia_libre=referencia_libre,
        trabajo=trabajo,
        material_trabajo=material_trabajo,
        orden_compra=orden_compra,
        linea_orden_compra=linea_orden_compra,
        registrado_por=usuario,
    )
    log_action(
        usuario,
        "registrar_movimiento_stock",
        movimiento,
        detail=detalle or f"{movimiento.get_tipo_display()} {cantidad} de {producto} ({deposito})",
    )
    return movimiento


def cantidad_pendiente_devolucion(salida):
    """
    Solo tiene sentido si salida.requiere_devolucion=True. `cantidad`
    en una Salida se guarda negativa; las Devoluciones se guardan
    positivas — no se persiste ningún estado, se deriva del ledger.
    """
    enviado = abs(salida.cantidad)
    devuelto = salida.devoluciones.aggregate(total=Sum("cantidad"))["total"] or Decimal("0")
    return enviado - devuelto


def salidas_repuestos_pendientes():
    """Salidas de repuestos con devolución pendiente (total o parcial)."""
    return [
        salida
        for salida in MovimientoStock.objects.filter(
            tipo=TipoMovimiento.SALIDA, deposito=Deposito.REPUESTOS, requiere_devolucion=True
        ).select_related("producto")
        if cantidad_pendiente_devolucion(salida) > 0
    ]
