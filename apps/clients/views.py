from django.db import models as db_models
from django.urls import reverse
from django.views.generic import CreateView, ListView, UpdateView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin

from .forms import ClienteForm
from .models import Cliente


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
