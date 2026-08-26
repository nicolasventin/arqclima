from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class HomeView(LoginRequiredMixin, TemplateView):
    """
    Dashboard base por usuario. En esta etapa solo muestra un placeholder
    según el rol; los widgets reales (presupuestos pendientes, stock bajo,
    tareas asignadas, etc.) se conectan en etapas futuras.
    """

    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["puede_gestionar_permisos"] = self.request.user.has_perm(
            "accounts.manage_permissions"
        )
        return context
