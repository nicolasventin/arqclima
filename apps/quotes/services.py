from decimal import Decimal

from apps.audit.services import log_action
from apps.pricing.models import ConfiguracionGeneral

from .models import EstadoPresupuesto, TipoDescuento, TipoIVA


def sugerir_costo_mano_obra(precio_unitario):
    """
    Costo sugerido para un ítem manual a partir de
    ConfiguracionGeneral.margen_mano_obra (Etapa 3): costo = precio /
    (1 + margen_mano_obra/100). Es solo un valor por defecto para
    precargar en el formulario de carga del ítem — Rodrigo o Diego
    pueden sobrescribirlo si el costo real de ese ítem puntual es
    distinto.
    """
    margen = ConfiguracionGeneral.obtener().margen_mano_obra
    return (precio_unitario / (Decimal("1") + margen / Decimal("100"))).quantize(Decimal("0.01"))


def _neto_item(item, iva_pct):
    subtotal = item.cantidad * item.precio_unitario
    neto = subtotal * (Decimal("1") - item.descuento_pct / Decimal("100"))
    if item.tipo_iva == TipoIVA.MAS_IVA:
        neto = neto * (Decimal("1") + iva_pct / Decimal("100"))
    return neto


def calcular_totales(presupuesto):
    """
    Desglose completo del presupuesto (no solo el total final), para
    poder mostrarlo paso a paso en la UI.

    Con descuento general porcentual, cantidad_unidades multiplica
    DESPUÉS de aplicar el descuento (multiplica el total ya
    descontado). Con descuento general de monto fijo, el monto se
    resta UNA SOLA VEZ sobre el total ya multiplicado por
    cantidad_unidades: si se restara antes de multiplicar, un
    descuento fijo de $100.000 con cantidad_unidades=3 terminaría
    restando $300.000 en la práctica, que no es la intención de un
    monto fijo.
    """
    config = ConfiguracionGeneral.obtener()
    items = presupuesto.items.filter(incluido=True)

    subtotal_general = sum((_neto_item(item, config.iva_pct) for item in items), Decimal("0"))

    if presupuesto.descuento_general_tipo == TipoDescuento.PORCENTAJE:
        subtotal_con_descuento = subtotal_general * (
            Decimal("1") - presupuesto.descuento_general_valor / Decimal("100")
        )
        total_final = subtotal_con_descuento * presupuesto.cantidad_unidades
    else:
        total_final = (
            subtotal_general * presupuesto.cantidad_unidades
        ) - presupuesto.descuento_general_valor

    return {
        "subtotal_general": subtotal_general.quantize(Decimal("0.01")),
        "descuento_general_tipo": presupuesto.descuento_general_tipo,
        "descuento_general_valor": presupuesto.descuento_general_valor,
        "cantidad_unidades": presupuesto.cantidad_unidades,
        "total_final": total_final.quantize(Decimal("0.01")),
    }


def margen_item(item):
    """
    Margen (%) del ítem ya con su descuento aplicado, como markup sobre
    costo — mismo criterio que pricing.services.calcular_precio_venta,
    no margen bruto sobre precio. None si el ítem no tiene
    costo_unitario cargado (ej. un manual sin costo a mano): en ese
    caso queda fuera del chequeo de margen bajo.
    """
    if not item.costo_unitario:
        return None
    precio_con_descuento = item.precio_unitario * (
        Decimal("1") - item.descuento_pct / Decimal("100")
    )
    return ((precio_con_descuento / item.costo_unitario) - Decimal("1")) * Decimal("100")


def enviar_presupuesto(presupuesto, usuario):
    """
    Pasa el presupuesto a estado Enviado. Regla de negocio 6: un margen
    bajo NO bloquea el envío, pero queda registrado en auditoría quién
    lo mandó así. Se evalúa en CADA transición a Enviado, incluidos
    reenvíos tras editar un presupuesto ya enviado.
    """
    config = ConfiguracionGeneral.obtener()
    items_con_margen_bajo = []
    for item in presupuesto.items.filter(incluido=True):
        margen = margen_item(item)
        if margen is not None and margen < config.margen_minimo_alerta:
            items_con_margen_bajo.append((item, margen))

    presupuesto.estado = EstadoPresupuesto.ENVIADO
    presupuesto.save(update_fields=["estado"])

    if items_con_margen_bajo:
        detalle = "; ".join(f"{item} ({margen:.2f}%)" for item, margen in items_con_margen_bajo)
        log_action(
            usuario,
            "enviar_presupuesto_margen_bajo",
            presupuesto,
            detail=(
                f"Enviado con margen por debajo del mínimo "
                f"({config.margen_minimo_alerta}%): {detalle}"
            ),
        )
    else:
        log_action(usuario, "enviar_presupuesto", presupuesto)

    return presupuesto
