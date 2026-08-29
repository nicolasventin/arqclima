from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin

from .forms import TareaForm
from .models import EstadoTarea, Tarea
from .permissions import puede_actualizar_estado, puede_gestionar_tareas, queryset_tareas_visibles
from .services import TransicionInvalidaError, cambiar_estado_tarea


class TareaListView(PermisoRequeridoMixin, ListView):
    """
    Lista de tareas con el alcance de visibilidad por rol: Diego ve todo
    el equipo, Rodrigo/Gabriel lo que asignaron + lo propio, Contri/
    Andrés solo lo propio (aunque para "solo lo propio" ya está
    MisTareasView — esta vista sigue siendo útil para Rodrigo/Gabriel
    como panel de seguimiento de lo que asignaron).
    """

    model = Tarea
    permission_required = "tasks.view_tarea"
    template_name = "tasks/tarea_list.html"
    context_object_name = "tareas"
    paginate_by = 50

    def get_queryset(self):
        qs = Tarea.objects.select_related("asignado_a", "asignado_por")
        estado = self.request.GET.get("estado")
        q = (self.request.GET.get("q") or "").strip()
        if estado:
            qs = qs.filter(estado=estado)
        else:
            qs = qs.exclude(estado=EstadoTarea.COMPLETADA)
        if q:
            qs = qs.filter(
                Q(titulo__icontains=q)
                | Q(descripcion__icontains=q)
                | Q(asignado_a__username__icontains=q)
                | Q(asignado_a__first_name__icontains=q)
                | Q(asignado_a__last_name__icontains=q)
            )
        return queryset_tareas_visibles(self.request.user, qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estados"] = EstadoTarea.choices
        context["puede_gestionar"] = puede_gestionar_tareas(self.request.user)
        return context


class MisTareasView(PermisoRequeridoMixin, ListView):
    model = Tarea
    permission_required = "tasks.view_tarea"
    template_name = "tasks/mis_tareas.html"
    context_object_name = "tareas"

    def get_queryset(self):
        return (
            Tarea.objects.filter(asignado_a=self.request.user)
            .exclude(estado=EstadoTarea.COMPLETADA)
            .select_related("asignado_por")
        )


class TareaCreateView(UserPassesTestMixin, CreateView):
    model = Tarea
    form_class = TareaForm
    template_name = "tasks/tarea_form.html"
    raise_exception = True

    def test_func(self):
        return puede_gestionar_tareas(self.request.user)

    def form_valid(self, form):
        form.instance.asignado_por = self.request.user
        response = super().form_valid(form)
        log_action(
            self.request.user, "create_tarea", self.object,
            f"Tarea creada y asignada a {self.object.asignado_a}: {self.object}",
        )
        return response

    def get_success_url(self):
        return reverse("tasks:lista")


class TareaUpdateView(UserPassesTestMixin, UpdateView):
    model = Tarea
    form_class = TareaForm
    template_name = "tasks/tarea_form.html"
    raise_exception = True

    def test_func(self):
        return puede_gestionar_tareas(self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request.user, "update_tarea", self.object, f"Tarea editada: {self.object}")
        return response

    def get_success_url(self):
        return reverse("tasks:lista")


class CambiarEstadoTareaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.tarea = get_object_or_404(Tarea, pk=self.kwargs["pk"])
        return puede_actualizar_estado(self.request.user, self.tarea)

    def post(self, request, pk):
        nuevo_estado = request.POST.get("estado")
        try:
            cambiar_estado_tarea(self.tarea, nuevo_estado, request.user)
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        next_url = request.POST.get("next") or reverse("dashboard:home")
        return redirect(next_url)
