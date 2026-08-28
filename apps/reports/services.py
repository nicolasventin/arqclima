from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum

from apps.audit.models import AuditLog
from apps.catalog.models import Producto
from apps.jobs.models import EstadoTrabajo, MaterialTrabajo, Trabajo
from apps.jobs.services import cantidad_enviada, cantidad_usada_neta
from apps.pricing.services import ultimo_costo_producto
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, TipoDescuento
from apps.quotes.services import calcular_totales, margen_item
from apps.stock.models import Deposito, MovimientoStock, TipoMovimiento
from apps.stock.services import productos_con_stock_bajo, stock_actual

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


# --- Parte 3: Rentabilidad ---
#
# A diferencia de Comercial (que solo cuenta/promedia estados y montos ya
# calculados por quotes.services), acá se necesita un costo real por ítem
# — con dos fuentes distintas según haya o no MaterialTrabajo vinculado
# (ver ganancia_trabajo). "Realizado" reusa presupuestos_realizados_en(),
# mismo criterio de período que Comercial.


def ganancia_presupuesto(presupuesto):
    """
    total_final (ya con el descuento general aplicado) menos el costo
    cotizado de los ítems incluidos con costo cargado. A propósito NO es
    sum(item.ganancia_item() para item en...) — eso no descontaría el
    descuento general del presupuesto, solo el de cada ítem.

    El costo de cada ítem se escala por cantidad_unidades para quedar en
    la misma base que total_final (que ya viene multiplicado) — mismo
    criterio que ganancia_trabajo() para ítems sin MaterialTrabajo, y
    mismo motivo que el fix de generar_listado_materiales() (commit
    cf380c6): comparar un ingreso a escala completa contra un costo a
    escala de una sola unidad infla la ganancia para cantidad_unidades > 1.
    """
    totales = calcular_totales(presupuesto)
    costo_por_unidad = sum(
        (
            item.costo_unitario * item.cantidad
            for item in presupuesto.items.filter(incluido=True)
            if item.costo_unitario is not None
        ),
        Decimal("0"),
    )
    costo_total = costo_por_unidad * presupuesto.cantidad_unidades
    return totales["total_final"] - costo_total


def ganancia_trabajo(trabajo):
    """
    Ingreso = total_final del presupuesto de origen, completo y fijo (el
    cliente paga el precio pactado, regla de negocio 8 — nunca se
    reescala por consumo real). Costo, ítem por ítem:

    - Con MaterialTrabajo vinculado (item.materiales_trabajo): costo_unitario
      × cantidad_usada_neta() sumada de esos materiales — ya a escala
      completa desde que se corrigió generar_listado_materiales().
    - Sin MaterialTrabajo vinculado (conceptos manuales como "Mano de
      obra", que generar_listado_materiales() nunca copia — decisión 38):
      costo_unitario × item.cantidad × cantidad_unidades, mismos dos
      factores de escala que el lado del ingreso. Sin descuento_pct ni
      IVA: son ajustes de precio de venta, no tocan costo.

    Ítems sin costo_unitario cargado quedan fuera de la suma y prenden
    tiene_costos_incompletos, para que la UI pueda avisar que el número
    es parcial en vez de mostrarlo como si fuera exacto.
    """
    presupuesto = trabajo.presupuesto
    ingreso = calcular_totales(presupuesto)["total_final"]

    costo_total = Decimal("0")
    tiene_costos_incompletos = False
    for item in presupuesto.items.filter(incluido=True):
        if item.costo_unitario is None:
            tiene_costos_incompletos = True
            continue

        materiales = list(item.materiales_trabajo.filter(trabajo=trabajo))
        if materiales:
            cantidad = sum((cantidad_usada_neta(m) for m in materiales), Decimal("0"))
        else:
            cantidad = item.cantidad * presupuesto.cantidad_unidades
        costo_total += item.costo_unitario * cantidad

    return {
        "ingreso": ingreso,
        "costo": costo_total,
        "ganancia": ingreso - costo_total,
        "tiene_costos_incompletos": tiene_costos_incompletos,
    }


def trabajos_terminados_en(anio, mes):
    """Trabajo en estado Terminado cuyo presupuesto de origen fue "realizado" en el período."""
    return Trabajo.objects.filter(
        estado=EstadoTrabajo.TERMINADO,
        presupuesto__in=presupuestos_realizados_en(anio, mes),
    ).select_related("presupuesto", "presupuesto__cliente")


def metricas_rentabilidad(anio, mes):
    """
    Margen promedio y top de productos por margen — en %, no en $, así
    que (a diferencia de montos_rentabilidad) no dependen de
    reports.view_montos_confidenciales. margen_item() se usa tal cual
    (sin ajustar por descuento general, a propósito: acá se mide
    rentabilidad inherente del producto, no el resultado de una
    negociación puntual — ver decisión de diseño confirmada).
    """
    items = ItemPresupuesto.objects.filter(
        presupuesto__in=presupuestos_realizados_en(anio, mes), incluido=True
    ).select_related("producto")

    margenes = []
    margenes_por_producto = defaultdict(list)
    for item in items:
        margen = margen_item(item)
        if margen is None:
            continue
        margenes.append(margen)
        if item.producto_id is not None:
            margenes_por_producto[item.producto].append(margen)

    margen_promedio = (sum(margenes) / len(margenes)) if margenes else None

    productos_mejor_margen = sorted(
        (
            {"producto": producto, "margen_promedio": sum(valores) / len(valores)}
            for producto, valores in margenes_por_producto.items()
        ),
        key=lambda fila: fila["margen_promedio"],
        reverse=True,
    )[:10]

    return {
        "margen_promedio": margen_promedio,
        "productos_mejor_margen": productos_mejor_margen,
    }


def montos_rentabilidad(anio, mes):
    """
    Bloque confidencial (mismo permiso que Comercial): ganancia por
    presupuesto y por trabajo del período, con sus totales. El caller
    es responsable de chequear reports.view_montos_confidenciales antes
    de llamar — mismo criterio que montos_comerciales().
    """
    filas_presupuestos = [
        {"presupuesto": presupuesto, "ganancia": ganancia_presupuesto(presupuesto)}
        for presupuesto in presupuestos_realizados_en(anio, mes)
    ]
    filas_trabajos = [
        {"trabajo": trabajo, **ganancia_trabajo(trabajo)}
        for trabajo in trabajos_terminados_en(anio, mes)
    ]

    return {
        "ganancia_presupuestos": filas_presupuestos,
        "ganancia_total_presupuestos": sum(
            (fila["ganancia"] for fila in filas_presupuestos), Decimal("0")
        ),
        "ganancia_trabajos": filas_trabajos,
        "ganancia_total_trabajos": sum(
            (fila["ganancia"] for fila in filas_trabajos), Decimal("0")
        ),
    }


# --- Parte 3: Stock ---
#
# Mezcla a propósito dos tipos de métrica: foto actual (sin filtro de
# período — stock no tiene "actividad" fuera de sus movimientos) y
# actividad del período (con el mismo filtro mes/año que el resto de la
# Etapa 9).


def stock_valorizado():
    """
    Foto actual (montos, gated por view_montos_confidenciales): Σ
    stock_actual(producto, depósito) × ultimo_costo_producto(producto),
    para cada (producto, depósito) con stock ≠ 0 y algún costo cargado.
    Repuestos solo se evalúa para es_repuesto=True, mismo criterio que
    productos_con_stock_bajo().
    """
    detalle = []
    total = Decimal("0")
    for producto in Producto.objects.filter(activo=True):
        historial = ultimo_costo_producto(producto)
        if historial is None:
            continue

        depositos = [Deposito.GENERAL]
        if producto.es_repuesto:
            depositos.append(Deposito.REPUESTOS)

        for deposito in depositos:
            cantidad = stock_actual(producto, deposito)
            if cantidad == 0:
                continue
            valor = (cantidad * historial.costo).quantize(Decimal("0.01"))
            total += valor
            detalle.append(
                {
                    "producto": producto,
                    "deposito": deposito,
                    "cantidad": cantidad,
                    "costo_unitario": historial.costo,
                    "valor": valor,
                }
            )

    return {"total": total, "detalle": detalle}


def material_mas_utilizado(anio, mes, limite=10):
    """
    Ranking de actividad del período: Σ cantidad de MovimientoStock tipo
    Salida, directo del ledger — no pasa por Trabajo/MaterialTrabajo, así
    también cubre las salidas de repuestos de Gabriel (que no tienen
    trabajo asociado). cantidad de una Salida se guarda negativa
    (decisión 27), por eso se ordena ascendente (más negativo = más
    usado) y se muestra en valor absoluto.
    """
    filas = (
        MovimientoStock.objects.filter(
            tipo=TipoMovimiento.SALIDA, creado_en__year=anio, creado_en__month=mes
        )
        .values("producto__id", "producto__nombre", "producto__codigo", "producto__marca__nombre")
        .annotate(total=Sum("cantidad"))
        .order_by("total")[:limite]
    )
    return [
        {
            "producto_id": fila["producto__id"],
            "producto_nombre": fila["producto__nombre"],
            "producto_codigo": fila["producto__codigo"],
            "marca_nombre": fila["producto__marca__nombre"],
            "cantidad": abs(fila["total"]),
        }
        for fila in filas
    ]


def diferencia_enviado_utilizado(anio, mes):
    """
    Actividad del período: MaterialTrabajo con algún MovimientoStock
    (envío o devolución) dentro del mes, listando los que tienen
    diferencia ≠ 0 entre cantidad_enviada() y cantidad_usada_neta() —
    ambas funciones reusadas tal cual (acumulado histórico del material,
    no acotado al período); el filtro de período decide QUÉ materiales
    entran al listado, no reinterpreta esas dos funciones.
    """
    material_ids = (
        MovimientoStock.objects.filter(
            material_trabajo__isnull=False, creado_en__year=anio, creado_en__month=mes
        )
        .values_list("material_trabajo_id", flat=True)
        .distinct()
    )
    materiales = MaterialTrabajo.objects.filter(pk__in=material_ids).select_related(
        "producto", "trabajo"
    )

    resultado = []
    for material in materiales:
        enviado = cantidad_enviada(material)
        usado = cantidad_usada_neta(material)
        diferencia = enviado - usado
        if diferencia != 0:
            resultado.append(
                {
                    "material": material,
                    "enviado": enviado,
                    "usado": usado,
                    "diferencia": diferencia,
                }
            )
    return resultado


def metricas_stock(anio, mes):
    """
    Bloque sin montos (reports.view_reporte_stock alcanza, sin necesitar
    view_montos_confidenciales): poco stock (foto actual, reusa la
    Parte 1 tal cual), material más utilizado y diferencia enviado/usado
    (actividad del período).
    """
    return {
        "productos_stock_bajo": productos_con_stock_bajo(),
        "material_mas_utilizado": material_mas_utilizado(anio, mes),
        "diferencia_enviado_utilizado": diferencia_enviado_utilizado(anio, mes),
    }


def montos_stock():
    """
    Bloque confidencial de Stock: solo el valorizado ($). El caller
    chequea reports.view_montos_confidenciales antes de llamar, mismo
    criterio que montos_comerciales()/montos_rentabilidad(). Sin
    parámetro de período: es una foto actual, no actividad del mes.
    """
    return stock_valorizado()
