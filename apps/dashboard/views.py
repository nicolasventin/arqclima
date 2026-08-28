from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.jobs.models import EstadoTrabajo, Trabajo
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.stock.services import productos_con_stock_bajo
from apps.tasks.models import EstadoTarea, Tarea
from apps.tasks.permissions import puede_gestionar_tareas


class HomeView(LoginRequiredMixin, TemplateView):
    """
    Dashboard base por usuario. "Mis tareas" fue el primer widget
    (Etapa 6); "Presupuestos pendientes", "Stock bajo" y "Mis trabajos
    activos" se sumaron al cerrar la Etapa 9 (el plan original promete
    "dashboards" para esta etapa, además de las pantallas de reporte).
    Cada widget reusa exactamente los mismos datos que su reporte
    correspondiente (Comercial/Stock/Trabajos) — es un resumen a
    primera vista, no una fuente de verdad nueva — y se gatea con el
    mismo permiso que ese reporte, para no mostrarle a nadie un dato
    que no podría ver yendo a la pantalla completa.
    """

    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        context["puede_gestionar_permisos"] = user.has_perm("accounts.manage_permissions")

        context["mis_tareas"] = (
            Tarea.objects.filter(asignado_a=user)
            .exclude(estado=EstadoTarea.COMPLETADA)
            .order_by("fecha_limite")[:5]
        )
        context["puede_gestionar_tareas"] = puede_gestionar_tareas(user)

        if user.has_perm("reports.view_reporte_comercial"):
            context["presupuestos_pendientes"] = Presupuesto.objects.filter(
                estado=EstadoPresupuesto.ENVIADO
            ).count()

        if user.has_perm("reports.view_reporte_stock"):
            stock_bajo = productos_con_stock_bajo()
            context["stock_bajo"] = stock_bajo[:5]
            context["stock_bajo_total"] = len(stock_bajo)

        if user.has_perm("jobs.view_trabajo"):
            context["mis_trabajos_activos"] = (
                Trabajo.objects.filter(tecnico_asignado=user)
                .exclude(estado__in=[EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO])
                .count()
            )

        return context
