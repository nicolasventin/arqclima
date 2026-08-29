from datetime import date
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView

from apps.clients.models import Cliente
from apps.core.mixins import PermisoRequeridoMixin

from .permissions import puede_ver_montos_confidenciales
from .services import (
    clientes_con_mas_trabajos,
    historial_presupuestos_cliente,
    metricas_comerciales,
    metricas_empleados,
    metricas_rentabilidad,
    metricas_stock,
    montos_comerciales,
    montos_rentabilidad,
    montos_stock,
    presupuestos_pendientes_por_cliente,
)




def _decimal(valor):
    if valor is None:
        return Decimal("0")
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def _porcentaje_css(valor, maximo):
    maximo = _decimal(maximo)
    if maximo <= 0:
        return "0%"
    porcentaje = (abs(_decimal(valor)) / maximo) * Decimal("100")
    return f"{porcentaje.quantize(Decimal('0.1'))}%"


def _barras_relativas(filas, clave_valor):
    """
    Agrega ancho visual normalizado sin mover cálculo de negocio a la UI.

    Las métricas siguen saliendo de services.py; acá solo se transforma
    el valor a un ancho CSS para poder dibujar barras sin una librería JS.
    """
    filas = [dict(fila) for fila in filas]
    maximo = max(
        (abs(_decimal(fila.get(clave_valor))) for fila in filas),
        default=Decimal("0"),
    )
    for fila in filas:
        valor = _decimal(fila.get(clave_valor))
        fila["chart_width"] = _porcentaje_css(valor, maximo)
        fila["chart_negative"] = valor < 0
    return filas


def _distribucion_comercial(metricas):
    filas = [
        {"label": "Aceptados", "value": metricas["aceptados"], "tone": "success"},
        {"label": "Enviados sin resolver", "value": metricas["enviados_sin_resolver"], "tone": "info"},
        {"label": "Rechazados", "value": metricas["rechazados"], "tone": "danger"},
        {"label": "Vencidos", "value": metricas["vencidos"], "tone": "warning"},
        {"label": "Cancelados", "value": metricas["cancelados"], "tone": "neutral"},
        {"label": "Reabiertos a Borrador", "value": metricas["reabiertos_a_borrador"], "tone": "muted"},
    ]
    total = metricas["total_realizados"]
    for fila in filas:
        porcentaje = (
            Decimal(fila["value"]) / Decimal(total) * Decimal("100")
            if total
            else Decimal("0")
        )
        fila["percentage"] = porcentaje
        fila["chart_width"] = f"{porcentaje.quantize(Decimal('0.1'))}%"
    return filas


class _ReporteMensualView(PermisoRequeridoMixin, TemplateView):
    """
    Base común de navegación mes/año — usada por Comercial, Rentabilidad
    y Stock (Stock la usa también para su bloque de actividad del
    período, aunque parte de su contenido sea foto actual sin filtro).
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        hoy = timezone.localdate()
        anio = int(self.request.GET.get("anio", hoy.year))
        mes = int(self.request.GET.get("mes", hoy.month))

        context["anio"] = anio
        context["mes"] = mes
        context["periodo_fecha"] = date(anio, mes, 1)

        if mes == 1:
            context["anio_anterior"], context["mes_anterior"] = anio - 1, 12
        else:
            context["anio_anterior"], context["mes_anterior"] = anio, mes - 1
        if mes == 12:
            context["anio_siguiente"], context["mes_siguiente"] = anio + 1, 1
        else:
            context["anio_siguiente"], context["mes_siguiente"] = anio, mes + 1

        return context


class ReporteComercialView(_ReporteMensualView):
    template_name = "reports/comercial.html"
    permission_required = "reports.view_reporte_comercial"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metricas"] = metricas_comerciales(context["anio"], context["mes"])
        context["chart_estados"] = _distribucion_comercial(context["metricas"])

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_comerciales(context["anio"], context["mes"])

        return context


class ReporteRentabilidadView(_ReporteMensualView):
    template_name = "reports/rentabilidad.html"
    permission_required = "reports.view_reporte_rentabilidad"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metricas"] = metricas_rentabilidad(context["anio"], context["mes"])
        context["chart_productos_margen"] = _barras_relativas(
            context["metricas"]["productos_mejor_margen"],
            "margen_promedio",
        )

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_rentabilidad(context["anio"], context["mes"])
            context["chart_ganancia_presupuestos"] = _barras_relativas(
                context["montos"]["ganancia_presupuestos"][:8],
                "ganancia",
            )
            context["chart_ganancia_trabajos"] = _barras_relativas(
                context["montos"]["ganancia_trabajos"][:8],
                "ganancia",
            )

        return context


class ReporteStockView(_ReporteMensualView):
    template_name = "reports/stock.html"
    permission_required = "reports.view_reporte_stock"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metricas"] = metricas_stock(context["anio"], context["mes"])
        context["chart_material_uso"] = _barras_relativas(
            context["metricas"]["material_mas_utilizado"],
            "cantidad",
        )
        context["resumen_stock"] = {
            "stock_bajo": len(context["metricas"]["productos_stock_bajo"]),
            "productos_con_salida": len(context["metricas"]["material_mas_utilizado"]),
            "diferencias_materiales": len(context["metricas"]["diferencia_enviado_utilizado"]),
        }

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_stock()

        return context


class ReporteClientesView(PermisoRequeridoMixin, TemplateView):
    """
    Sin navegador de mes/año (a diferencia de Comercial/Rentabilidad/
    Stock): sus métricas son de estado actual o histórico completo, no
    de actividad de un mes puntual.
    """

    template_name = "reports/clientes.html"
    permission_required = "reports.view_reporte_clientes"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["clientes_con_mas_trabajos"] = clientes_con_mas_trabajos()
        context["presupuestos_pendientes_por_cliente"] = presupuestos_pendientes_por_cliente()
        context["chart_clientes_trabajos"] = _barras_relativas(
            context["clientes_con_mas_trabajos"],
            "cantidad_trabajos",
        )
        context["chart_clientes_pendientes"] = _barras_relativas(
            context["presupuestos_pendientes_por_cliente"][:10],
            "cantidad_pendientes",
        )
        context["resumen_clientes"] = {
            "clientes_con_trabajos_en_top": len(context["clientes_con_mas_trabajos"]),
            "clientes_con_pendientes": len(context["presupuestos_pendientes_por_cliente"]),
            "presupuestos_pendientes": sum(
                fila["cantidad_pendientes"]
                for fila in context["presupuestos_pendientes_por_cliente"]
            ),
            "max_trabajos_cliente": (
                context["clientes_con_mas_trabajos"][0]["cantidad_trabajos"]
                if context["clientes_con_mas_trabajos"]
                else 0
            ),
        }
        return context


class HistorialClienteView(PermisoRequeridoMixin, TemplateView):
    """
    Historial completo de un cliente puntual — sin gating de montos (ver
    la nota en reports.services sobre historial_presupuestos_cliente):
    son totales individuales, el mismo dato que ya se ve sin restricción
    en el listado general de presupuestos.
    """

    template_name = "reports/historial_cliente.html"
    permission_required = "reports.view_reporte_clientes"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = get_object_or_404(Cliente, pk=kwargs["pk"])
        context["cliente"] = cliente
        context["historial"] = historial_presupuestos_cliente(cliente)
        return context


# Etiquetas de presentación para las claves de "actividad" que arma
# actividad_administrativa_por_empleado() — a propósito NO viven en
# services.py: son las mismas claves snake_case que usan los tests
# (más estables que un string en español), esto es puramente cómo se
# muestran en la plantilla.
ETIQUETAS_ACTIVIDAD = {
    "presupuestos_creados": "Presupuestos creados",
    "presupuestos_enviados": "Presupuestos enviados",
    "servicios_registrados": "Servicios registrados",
    "repuestos_vendidos": "Repuestos vendidos",
    "ordenes_compra_creadas": "Órdenes de compra creadas",
    "movimientos_stock_registrados": "Movimientos de stock registrados",
    "trabajos_con_cambio_estado": "Trabajos con cambio de estado",
    "movimientos_stock_trabajos_propios": "Movimientos de stock de sus trabajos",
}


class ReporteEmpleadosView(_ReporteMensualView):
    """
    Mismo patrón mixto que Stock: dos bloques de foto actual (tareas,
    trabajos activos, dentro de metricas_empleados) y uno de actividad
    del período que sí usa el navegador de mes/año de esta base.
    """

    template_name = "reports/empleados.html"
    permission_required = "reports.view_reporte_empleados"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = metricas_empleados(context["anio"], context["mes"])
        for fila in filas:
            fila["actividad_etiquetada"] = [
                (ETIQUETAS_ACTIVIDAD.get(clave, clave), valor)
                for clave, valor in fila["actividad"].items()
            ]
        maximos = {
            "tareas_pendientes": max((fila["tareas_pendientes"] for fila in filas), default=0),
            "tareas_vencidas": max((fila["tareas_vencidas"] for fila in filas), default=0),
            "trabajos_activos": max((fila["trabajos_activos"] for fila in filas), default=0),
            "actividad_total": 0,
        }
        for fila in filas:
            fila["actividad_total"] = sum(fila["actividad"].values())
            maximos["actividad_total"] = max(maximos["actividad_total"], fila["actividad_total"])

        for fila in filas:
            fila["chart_tareas"] = _porcentaje_css(
                fila["tareas_pendientes"], maximos["tareas_pendientes"]
            )
            fila["chart_vencidas"] = _porcentaje_css(
                fila["tareas_vencidas"], maximos["tareas_vencidas"]
            )
            fila["chart_trabajos"] = _porcentaje_css(
                fila["trabajos_activos"], maximos["trabajos_activos"]
            )
            fila["chart_actividad"] = _porcentaje_css(
                fila["actividad_total"], maximos["actividad_total"]
            )

        context["filas"] = filas
        context["resumen_empleados"] = {
            "tareas_pendientes": sum(fila["tareas_pendientes"] for fila in filas),
            "tareas_vencidas": sum(fila["tareas_vencidas"] for fila in filas),
            "trabajos_activos": sum(fila["trabajos_activos"] for fila in filas),
            "actividad_periodo": sum(fila["actividad_total"] for fila in filas),
        }
        return context
