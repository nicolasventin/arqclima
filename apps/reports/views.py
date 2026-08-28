from django.utils import timezone
from django.views.generic import TemplateView

from apps.core.mixins import PermisoRequeridoMixin

from .permissions import puede_ver_montos_confidenciales
from .services import metricas_comerciales, montos_comerciales


class ReporteComercialView(PermisoRequeridoMixin, TemplateView):
    template_name = "reports/comercial.html"
    permission_required = "reports.view_reporte_comercial"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        hoy = timezone.localdate()
        anio = int(self.request.GET.get("anio", hoy.year))
        mes = int(self.request.GET.get("mes", hoy.month))

        context["anio"] = anio
        context["mes"] = mes
        context["metricas"] = metricas_comerciales(anio, mes)

        if mes == 1:
            context["anio_anterior"], context["mes_anterior"] = anio - 1, 12
        else:
            context["anio_anterior"], context["mes_anterior"] = anio, mes - 1
        if mes == 12:
            context["anio_siguiente"], context["mes_siguiente"] = anio + 1, 1
        else:
            context["anio_siguiente"], context["mes_siguiente"] = anio, mes + 1

        if puede_ver_montos_confidenciales(self.request.user):
            context["montos"] = montos_comerciales(anio, mes)

        return context
