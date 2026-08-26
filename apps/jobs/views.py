from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.mixins import PermisoRequeridoMixin
from apps.quotes.models import EstadoPresupuesto, Presupuesto

from .forms import CrearTrabajoForm
from .models import ORDEN_ESTADOS, EstadoTrabajo, Trabajo
from .permissions import puede_cambiar_estado_trabajo, puede_crear_trabajo, queryset_trabajos_visibles
from .services import TransicionInvalidaError, cambiar_estado_trabajo, crear_trabajo


class TrabajoListView(PermisoRequeridoMixin, ListView):
    model = Trabajo
    permission_required = "jobs.view_trabajo"
    template_name = "jobs/trabajo_list.html"
    context_object_name = "trabajos"
    paginate_by = 50

    def get_queryset(self):
        qs = Trabajo.objects.select_related("presupuesto__cliente", "tecnico_asignado")
        return queryset_trabajos_visibles(self.request.user, qs)


class TrabajoDetailView(PermisoRequeridoMixin, DetailView):
    model = Trabajo
    permission_required = "jobs.view_trabajo"
    template_name = "jobs/trabajo_detail.html"
    context_object_name = "trabajo"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trabajo = self.object
        idx_actual = ORDEN_ESTADOS.index(trabajo.estado)
        siguientes = [
            {"valor": estado, "etiqueta": EstadoTrabajo(estado).label}
            for estado in ORDEN_ESTADOS[idx_actual + 1 :]
            if puede_cambiar_estado_trabajo(self.request.user, trabajo, estado)
        ]
        context["estados_disponibles"] = siguientes
        return context


class CrearTrabajoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["presupuesto_pk"])
        return puede_crear_trabajo(self.request.user)

    def post(self, request, presupuesto_pk):
        form = CrearTrabajoForm(request.POST)
        if self.presupuesto.estado != EstadoPresupuesto.ACEPTADO:
            messages.error(request, "Solo se puede crear un Trabajo a partir de un Presupuesto Aceptado.")
            return redirect("quotes:detalle", pk=presupuesto_pk)
        if hasattr(self.presupuesto, "trabajo"):
            messages.error(request, "Este presupuesto ya tiene un Trabajo creado.")
            return redirect("quotes:detalle", pk=presupuesto_pk)
        if not form.is_valid():
            messages.error(request, "No se pudo crear el trabajo.")
            return redirect("quotes:detalle", pk=presupuesto_pk)

        trabajo = crear_trabajo(
            self.presupuesto, request.user, tecnico_asignado=form.cleaned_data["tecnico_asignado"]
        )
        messages.success(request, f"Trabajo #{trabajo.pk} creado.")
        return redirect("jobs:detalle", pk=trabajo.pk)


class CambiarEstadoTrabajoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        nuevo_estado = self.request.POST.get("estado")
        return puede_cambiar_estado_trabajo(self.request.user, self.trabajo, nuevo_estado)

    def post(self, request, pk):
        nuevo_estado = request.POST.get("estado")
        try:
            cambiar_estado_trabajo(self.trabajo, nuevo_estado, request.user)
            messages.success(request, f"Trabajo #{self.trabajo.pk}: {self.trabajo.get_estado_display()}.")
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        return redirect("jobs:detalle", pk=pk)
