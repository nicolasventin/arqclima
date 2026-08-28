from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from apps.audit.models import AuditLog
from apps.quotes.models import EstadoPresupuesto, Presupuesto, TipoDescuento
from apps.quotes.services import calcular_totales

ACCIONES_ENVIO = ["enviar_presupuesto", "enviar_presupuesto_margen_bajo"]


def presupuestos_realizados_en(anio, mes):
    """
    "Realizado" = se envió al menos una vez dentro del mes (AuditLog de
    enviar_presupuesto/enviar_presupuesto_margen_bajo), no "se creó en
    el mes": un Borrador que nunca se mandó no cuenta para el reporte
    Comercial. Es también la base de la tasa de conversión.
    """
    content_type = ContentType.objects.get_for_model(Presupuesto)
    object_ids = AuditLog.objects.filter(
        content_type=content_type,
        accion__in=ACCIONES_ENVIO,
        creado_en__year=anio,
        creado_en__month=mes,
    ).values_list("object_id", flat=True)
    # object_id es CharField (AuditLog es genérico vía GenericForeignKey):
    # se materializa a int en Python antes del filtro. Un pk__in con la
    # queryset original arma una subquery bigint = varchar que Postgres
    # rechaza (a diferencia de SQLite, que la castea sola).
    ids = {int(object_id) for object_id in object_ids}
    return Presupuesto.objects.filter(pk__in=ids)


def _descuento_efectivo_pct(presupuesto, totales):
    """
    Solo descuento GENERAL del presupuesto — decisión de alcance de la
    Parte 2 (Etapa 9): no se desglosa por ítem. Con tipo Porcentaje se
    usa el valor cargado directo; con Monto fijo se convierte a un %
    equivalente sobre el total antes de aplicar ese descuento, para
    poder promediarlo junto con los porcentuales.
    """
    if presupuesto.descuento_general_tipo == TipoDescuento.PORCENTAJE:
        return presupuesto.descuento_general_valor

    base = totales["subtotal_general"] * totales["cantidad_unidades"]
    if base == 0:
        return Decimal("0")
    return (presupuesto.descuento_general_valor / base) * Decimal("100")


def metricas_comerciales(anio, mes):
    """
    Métricas estructurales del reporte Comercial: nunca incluyen
    montos, así que no dependen de reports.view_montos_confidenciales
    (ver montos_comerciales() para la parte restringida a Diego).

    Los estados se leen AL MOMENTO de generar el reporte, no "resueltos
    dentro del período" — un presupuesto enviado en agosto y aceptado
    en septiembre cuenta como Aceptado si se regenera el reporte de
    agosto más tarde. Es una simplificación a propósito (ver diseño);
    la UI tiene que dejarlo explícito para que no se lea como una foto
    congelada del mes.
    """
    realizados = list(presupuestos_realizados_en(anio, mes))
    total_realizados = len(realizados)

    conteo_por_estado = dict.fromkeys(EstadoPresupuesto.values, 0)
    descuentos = []
    for presupuesto in realizados:
        conteo_por_estado[presupuesto.estado] += 1
        descuentos.append(_descuento_efectivo_pct(presupuesto, calcular_totales(presupuesto)))

    aceptados = conteo_por_estado[EstadoPresupuesto.ACEPTADO]
    tasa_conversion = (
        (Decimal(aceptados) / Decimal(total_realizados) * Decimal("100"))
        if total_realizados
        else None
    )
    descuento_promedio = (sum(descuentos) / len(descuentos)) if descuentos else None

    return {
        "total_realizados": total_realizados,
        "aceptados": aceptados,
        "rechazados": conteo_por_estado[EstadoPresupuesto.RECHAZADO],
        "vencidos": conteo_por_estado[EstadoPresupuesto.VENCIDO],
        "cancelados": conteo_por_estado[EstadoPresupuesto.CANCELADO],
        "enviados_sin_resolver": conteo_por_estado[EstadoPresupuesto.ENVIADO],
        "reabiertos_a_borrador": conteo_por_estado[EstadoPresupuesto.BORRADOR],
        "tasa_conversion": tasa_conversion,
        "descuento_promedio": descuento_promedio,
    }


def montos_comerciales(anio, mes):
    """
    Bloque confidencial (regla explícita de Diego): facturación
    potencial (Enviados sin resolver todavía) y facturación ya
    aceptada del período. El caller es responsable de chequear
    reports.view_montos_confidenciales ANTES de llamar a esta función
    — no hay un chequeo acá adentro a propósito, para que sea evidente
    en la vista que es una decisión explícita, no implícita.
    """
    realizados = presupuestos_realizados_en(anio, mes)

    facturacion_potencial = Decimal("0")
    facturacion_aceptada = Decimal("0")
    for presupuesto in realizados:
        total = calcular_totales(presupuesto)["total_final"]
        if presupuesto.estado == EstadoPresupuesto.ENVIADO:
            facturacion_potencial += total
        elif presupuesto.estado == EstadoPresupuesto.ACEPTADO:
            facturacion_aceptada += total

    return {
        "facturacion_potencial": facturacion_potencial,
        "facturacion_aceptada": facturacion_aceptada,
    }
