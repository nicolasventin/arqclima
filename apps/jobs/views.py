from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin
from apps.quotes.models import EstadoPresupuesto, Presupuesto

from .forms import AsignarTecnicoForm, CancelarTrabajoForm, CrearTrabajoForm
from .models import ORDEN_ESTADOS, EstadoTrabajo, Trabajo
from .permissions import (
    puede_asignar_tecnico,
    puede_cambiar_estado_trabajo,
    puede_cancelar_trabajo,
    puede_crear_trabajo,
    queryset_trabajos_visibles,
)
from .services import TransicionInvalidaError, cambiar_estado_trabajo, cancelar_trabajo, crear_trabajo


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

        # Un trabajo Cancelado queda afuera de ORDEN_ESTADOS a propósito
        # (ver models.py) — no tiene avanzar/retroceder, solo existió.
        if trabajo.estado in ORDEN_ESTADOS:
            idx_actual = ORDEN_ESTADOS.index(trabajo.estado)

            def _opciones(estados):
                return [
                    {"valor": estado, "etiqueta": EstadoTrabajo(estado).label}
                    for estado in estados
                    if puede_cambiar_estado_trabajo(self.request.user, trabajo, estado)
                ]

            context["estados_para_avanzar"] = _opciones(ORDEN_ESTADOS[idx_actual + 1 :])
            context["estados_para_retroceder"] = _opciones(ORDEN_ESTADOS[:idx_actual])
        else:
            context["estados_para_avanzar"] = []
            context["estados_para_retroceder"] = []

        context["puede_asignar_tecnico"] = puede_asignar_tecnico(self.request.user)
        if context["puede_asignar_tecnico"]:
            context["asignar_tecnico_form"] = AsignarTecnicoForm(
                initial={"tecnico_asignado": trabajo.tecnico_asignado_id}
            )

        context["puede_cancelar"] = puede_cancelar_trabajo(self.request.user) and trabajo.estado not in (
            EstadoTrabajo.TERMINADO,
            EstadoTrabajo.CANCELADO,
        )
        if context["puede_cancelar"]:
            context["cancelar_trabajo_form"] = CancelarTrabajoForm()

        return context


class CrearTrabajoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["presupuesto_pk"])
        return puede_crear_trabajo(self.request.user)

    def post(self, request, presupuesto_pk):
        form = CrearTrabajoForm(
            request.POST, puede_asignar_tecnico=puede_asignar_tecnico(request.user)
        )
        if self.presupuesto.estado != EstadoPresupuesto.ACEPTADO:
            messages.error(request, "Solo se puede crear un Trabajo a partir de un Presupuesto Aceptado.")
            return redirect("quotes:detalle", pk=presupuesto_pk)
        if hasattr(self.presupuesto, "trabajo"):
            messages.error(request, "Este presupuesto ya tiene un Trabajo creado.")
            return redirect("quotes:detalle", pk=presupuesto_pk)
        if not form.is_valid():
            messages.error(request, "No se pudo crear el trabajo.")
            return redirect("quotes:detalle", pk=presupuesto_pk)

        tecnico = form.cleaned_data.get("tecnico_asignado")
        trabajo = crear_trabajo(self.presupuesto, request.user, tecnico_asignado=tecnico)
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


class AsignarTecnicoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_asignar_tecnico(self.request.user)

    def post(self, request, pk):
        form = AsignarTecnicoForm(request.POST)
        if form.is_valid():
            anterior = self.trabajo.tecnico_asignado
            nuevo = form.cleaned_data["tecnico_asignado"]
            self.trabajo.tecnico_asignado = nuevo
            self.trabajo.save(update_fields=["tecnico_asignado"])
            log_action(
                request.user, "asignar_tecnico_trabajo", self.trabajo,
                detail=f"{anterior} → {nuevo}",
            )
            messages.success(request, "Técnico asignado.")
        return redirect("jobs:detalle", pk=pk)


class CancelarTrabajoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_cancelar_trabajo(self.request.user)

    def post(self, request, pk):
        form = CancelarTrabajoForm(request.POST)
        motivo = form.cleaned_data.get("motivo", "") if form.is_valid() else ""
        try:
            cancelar_trabajo(self.trabajo, request.user, motivo=motivo)
            messages.success(request, f"Trabajo #{self.trabajo.pk} cancelado.")
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        return redirect("jobs:detalle", pk=pk)
