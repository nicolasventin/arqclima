from django.utils import timezone
from django.views.generic import TemplateView

from apps.core.mixins import PermisoRequeridoMixin

from .permissions import puede_ver_montos_confidenciales
from .services import (
    metricas_comerciales,
    metricas_rentabilidad,
    metricas_stock,
    montos_comerciales,
    montos_rentabilidad,
    montos_stock,
)


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

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_comerciales(context["anio"], context["mes"])

        return context


class ReporteRentabilidadView(_ReporteMensualView):
    template_name = "reports/rentabilidad.html"
    permission_required = "reports.view_reporte_rentabilidad"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metricas"] = metricas_rentabilidad(context["anio"], context["mes"])

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_rentabilidad(context["anio"], context["mes"])

        return context


class ReporteStockView(_ReporteMensualView):
    template_name = "reports/stock.html"
    permission_required = "reports.view_reporte_stock"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metricas"] = metricas_stock(context["anio"], context["mes"])

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_stock()

        return context
