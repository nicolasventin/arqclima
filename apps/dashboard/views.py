from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.tasks.models import EstadoTarea, Tarea
from apps.tasks.permissions import puede_gestionar_tareas


class HomeView(LoginRequiredMixin, TemplateView):
    """
    Dashboard base por usuario. Los widgets reales se van conectando
    etapa a etapa; "Mis tareas" es el primero (Etapa 6). El resto
    (presupuestos pendientes, stock bajo, etc.) se suma más adelante.
    """

    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["puede_gestionar_permisos"] = self.request.user.has_perm(
            "accounts.manage_permissions"
        )
        context["mis_tareas"] = (
            Tarea.objects.filter(asignado_a=self.request.user)
            .exclude(estado=EstadoTarea.COMPLETADA)
            .order_by("fecha_limite")[:5]
        )
        context["puede_gestionar_tareas"] = puede_gestionar_tareas(self.request.user)
        return context
