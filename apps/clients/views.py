from django.db import models as db_models
from django.http import JsonResponse
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin

from .forms import ClienteForm, ClienteRapidoForm
from .models import Cliente


def _cliente_json(cliente):
    detalle = []
    if cliente.cuit_dni:
        detalle.append(f"CUIT/DNI {cliente.cuit_dni}")
    if cliente.telefono:
        detalle.append(cliente.telefono)
    if cliente.email:
        detalle.append(cliente.email)
    return {
        "id": cliente.pk,
        "nombre": cliente.nombre,
        "tipo": cliente.get_tipo_display(),
        "detalle": " · ".join(detalle),
    }


class ClienteListView(PermisoRequeridoMixin, ListView):
    model = Cliente
    permission_required = "clients.view_cliente"
    template_name = "clients/cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 50

    def get_queryset(self):
        qs = Cliente.objects.all()
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(
                db_models.Q(nombre__icontains=q)
                | db_models.Q(cuit_dni__icontains=q)
                | db_models.Q(email__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["puede_crear"] = self.request.user.has_perm("clients.add_cliente")
        return context


class ClienteCreateView(PermisoRequeridoMixin, CreateView):
    model = Cliente
    form_class = ClienteForm
    permission_required = "clients.add_cliente"
    template_name = "clients/cliente_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request.user, "create_cliente", self.object, f"Cliente creado: {self.object}")
        return response

    def get_success_url(self):
        return reverse("clients:lista")


class ClienteUpdateView(PermisoRequeridoMixin, UpdateView):
    model = Cliente
    form_class = ClienteForm
    permission_required = "clients.change_cliente"
    template_name = "clients/cliente_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request.user, "update_cliente", self.object, f"Cliente editado: {self.object}")
        return response

    def get_success_url(self):
        return reverse("clients:lista")


class ClienteSearchView(PermisoRequeridoMixin, View):
    """Búsqueda server-side para selectores que pueden crecer a miles de clientes."""

    permission_required = "clients.view_cliente"

    def get(self, request):
        q = (request.GET.get("q") or "").strip()
        if len(q) < 2:
            return JsonResponse({"resultados": []})

        clientes = (
            Cliente.objects.filter(activo=True)
            .filter(
                db_models.Q(nombre__icontains=q)
                | db_models.Q(cuit_dni__icontains=q)
                | db_models.Q(telefono__icontains=q)
                | db_models.Q(email__icontains=q)
            )
            .order_by("nombre")[:20]
        )
        return JsonResponse({"resultados": [_cliente_json(cliente) for cliente in clientes]})


class ClienteQuickCreateView(PermisoRequeridoMixin, View):
    """Alta rápida de cliente sin abandonar el flujo que la invoca."""

    permission_required = "clients.add_cliente"

    def post(self, request):
        form = ClienteRapidoForm(request.POST)
        if not form.is_valid():
            errores = {
                campo: [error["message"] for error in lista]
                for campo, lista in form.errors.get_json_data().items()
            }
            return JsonResponse({"ok": False, "errores": errores}, status=400)

        cliente = form.save()
        log_action(
            request.user,
            "create_cliente",
            cliente,
            f"Cliente creado mediante alta rápida: {cliente}",
        )
        return JsonResponse({"ok": True, "cliente": _cliente_json(cliente)}, status=201)
