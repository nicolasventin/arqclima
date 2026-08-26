from decimal import Decimal

from apps.audit.services import log_action
from apps.pricing.models import ConfiguracionGeneral
from apps.pricing.services import calcular_precio_venta, costo_actual

from .models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, SeccionPresupuesto, TipoDescuento, TipoIVA


class TransicionInvalidaError(ValueError):
    pass


TRANSICIONES_VALIDAS = {
    EstadoPresupuesto.BORRADOR: {
        EstadoPresupuesto.ENVIADO,
        EstadoPresupuesto.CANCELADO,
    },
    EstadoPresupuesto.ENVIADO: {
        EstadoPresupuesto.ACEPTADO,
        EstadoPresupuesto.RECHAZADO,
        EstadoPresupuesto.VENCIDO,
        EstadoPresupuesto.CANCELADO,
        EstadoPresupuesto.BORRADOR,
    },
    EstadoPresupuesto.RECHAZADO: {EstadoPresupuesto.BORRADOR},
    EstadoPresupuesto.VENCIDO: {EstadoPresupuesto.BORRADOR},
    EstadoPresupuesto.ACEPTADO: {EstadoPresupuesto.CANCELADO},
    EstadoPresupuesto.CANCELADO: set(),
}


def cambiar_estado(presupuesto, nuevo_estado, usuario, accion=None, detalle=""):
    """
    Único punto de entrada para mover el estado de un presupuesto.
    Valida TRANSICIONES_VALIDAS (ej. Borrador→Aceptado directo o
    cualquier salida desde Cancelado están prohibidos) y audita
    SIEMPRE, no solo en los casos con lógica especial (ver
    enviar_presupuesto, que arma su propio `accion`/`detalle` para el
    caso de margen bajo).

    Revertir un Aceptado (→Cancelado) no se valida acá: ese permiso
    (quotes.revert_presupuesto_aceptado) se chequea en la vista, igual
    que el resto de los permisos del proyecto — ver
    apps.quotes.permissions.puede_revertir_aceptado.
    """
    estado_actual = presupuesto.estado
    permitidos = TRANSICIONES_VALIDAS.get(estado_actual, set())
    if nuevo_estado not in permitidos:
        raise TransicionInvalidaError(
            f"No se puede pasar de '{estado_actual}' a '{nuevo_estado}'."
        )

    presupuesto.estado = nuevo_estado
    presupuesto.save(update_fields=["estado"])

    log_action(
        usuario,
        accion or "cambiar_estado_presupuesto",
        presupuesto,
        detail=detalle or f"{estado_actual} → {nuevo_estado}",
    )
    return presupuesto


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

    if items_con_margen_bajo:
        detalle = "; ".join(f"{item} ({margen:.2f}%)" for item, margen in items_con_margen_bajo)
        return cambiar_estado(
            presupuesto,
            EstadoPresupuesto.ENVIADO,
            usuario,
            accion="enviar_presupuesto_margen_bajo",
            detalle=(
                f"Enviado con margen por debajo del mínimo "
                f"({config.margen_minimo_alerta}%): {detalle}"
            ),
        )

    return cambiar_estado(
        presupuesto, EstadoPresupuesto.ENVIADO, usuario, accion="enviar_presupuesto"
    )


def duplicar_presupuesto(original, usuario):
    """
    Crea un presupuesto nuevo en Borrador a partir de uno existente,
    recalculando precios/costos con datos actuales (regla de negocio 8:
    "se puede duplicar y recalcular con precios actuales si se quiere").

    - Ítems de catálogo: se recalculan desde el MISMO producto_proveedor
      que tenía el ítem original — nunca se auto-elige un proveedor
      nuevo (regla de negocio 2). Si ese proveedor no tiene ningún
      costo cargado todavía, se mantienen los valores congelados del
      original tal cual, para que se revisen a mano en el nuevo
      borrador.
    - Ítems manuales (sin producto): precio_unitario se copia tal cual
      (no hay contra qué refrescarlo); costo_unitario se recalcula con
      sugerir_costo_mano_obra() usando el margen_mano_obra ACTUAL.
    - La plantilla de condiciones es la MISMA que tenía el original,
      pero el texto de condiciones se refresca desde esa plantilla por
      si se editó desde que se creó el original.
    """
    condiciones = (
        original.plantilla_condiciones.texto
        if original.plantilla_condiciones
        else original.condiciones
    )

    nuevo = Presupuesto.objects.create(
        cliente=original.cliente,
        direccion=original.direccion,
        cantidad_unidades=original.cantidad_unidades,
        descuento_general_tipo=original.descuento_general_tipo,
        descuento_general_valor=original.descuento_general_valor,
        notas_generales=original.notas_generales,
        plantilla_condiciones=original.plantilla_condiciones,
        condiciones=condiciones,
        creado_por=usuario,
    )

    mapa_secciones = {
        seccion.pk: SeccionPresupuesto.objects.create(
            presupuesto=nuevo, titulo=seccion.titulo, orden=seccion.orden
        )
        for seccion in original.secciones.all()
    }

    for item in original.items.all():
        precio_unitario = item.precio_unitario
        costo_unitario = item.costo_unitario

        if item.producto_proveedor is not None:
            historial = costo_actual(item.producto_proveedor)
            if historial is not None:
                costo_unitario = historial.costo
                precio_unitario, _ = calcular_precio_venta(item.producto, costo_unitario)
        elif item.producto is None:
            costo_unitario = sugerir_costo_mano_obra(precio_unitario)

        ItemPresupuesto.objects.create(
            presupuesto=nuevo,
            seccion=mapa_secciones.get(item.seccion_id),
            producto=item.producto,
            producto_proveedor=item.producto_proveedor,
            descripcion_manual=item.descripcion_manual,
            cantidad=item.cantidad,
            precio_unitario=precio_unitario,
            costo_unitario=costo_unitario,
            descuento_pct=item.descuento_pct,
            tipo_iva=item.tipo_iva,
            opcional=item.opcional,
            incluido=item.incluido,
            orden=item.orden,
        )

    log_action(usuario, "duplicar_presupuesto", nuevo, detail=f"Duplicado desde {original}")
    return nuevo
