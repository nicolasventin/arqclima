from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import models as db_models
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from apps.audit.services import log_action
from apps.catalog.models import Categoria, Marca, Producto, ProductoProveedor

from .forms import (
    ConfiguracionGeneralForm,
    MargenCategoriaForm,
    MargenMarcaForm,
    MargenProductoForm,
    RegistrarCostoForm,
)
from .models import ConfiguracionGeneral
from .permissions import puede_gestionar_margenes, puede_registrar_costo
from .services import registrar_costo


def _redirigir_seguro(request, destino_por_defecto, **kwargs):
    """
    Redirige a request.POST['next'] si es una ruta local segura (empieza
    con '/'); si no vino o no es segura, usa el destino por defecto.
    Se usa para que el margen de un producto se pueda editar tanto desde
    su ficha como desde la pantalla central de configuración, volviendo
    cada vez a donde estaba el usuario.
    """
    next_url = request.POST.get("next")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(destino_por_defecto, **kwargs)


class RegistrarCostoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.relacion = get_object_or_404(
            ProductoProveedor,
            pk=self.kwargs["relacion_pk"],
            producto_id=self.kwargs["producto_pk"],
        )
        return puede_registrar_costo(self.request.user, self.relacion.producto)

    def post(self, request, producto_pk, relacion_pk):
        form = RegistrarCostoForm(request.POST)
        if form.is_valid():
            registrar_costo(self.relacion, form.cleaned_data["costo"], request.user)
            log_action(
                request.user,
                "registrar_costo",
                self.relacion.producto,
                f"Nuevo costo para {self.relacion}: ${form.cleaned_data['costo']}",
            )
        return redirect("catalog:producto_detalle", pk=producto_pk)


class ActualizarMargenProductoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        return puede_gestionar_margenes(self.request.user)

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        form = MargenProductoForm(request.POST, instance=producto)
        if form.is_valid():
            form.save()
            log_action(
                request.user,
                "actualizar_margen_producto",
                producto,
                f"Margen propio actualizado a {producto.margen}",
            )
        return _redirigir_seguro(request, "catalog:producto_detalle", pk=pk)


class ActualizarMargenCategoriaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        return puede_gestionar_margenes(self.request.user)

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk)
        form = MargenCategoriaForm(request.POST, instance=categoria)
        if form.is_valid():
            form.save()
            log_action(
                request.user,
                "actualizar_margen_categoria",
                categoria,
                f"Margen de categoría actualizado a {categoria.margen}",
            )
        return redirect("pricing:configuracion")


class ActualizarMargenMarcaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        return puede_gestionar_margenes(self.request.user)

    def post(self, request, pk):
        marca = get_object_or_404(Marca, pk=pk)
        form = MargenMarcaForm(request.POST, instance=marca)
        if form.is_valid():
            form.save()
            log_action(
                request.user,
                "actualizar_margen_marca",
                marca,
                f"Margen de marca actualizado a {marca.margen}",
            )
        return redirect("pricing:configuracion")


class ActualizarConfiguracionGeneralView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        return puede_gestionar_margenes(self.request.user)

    def post(self, request):
        config = ConfiguracionGeneral.obtener()
        form = ConfiguracionGeneralForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            log_action(
                request.user,
                "actualizar_configuracion_precios",
                config,
                "Configuración general de precios actualizada",
            )
        return redirect("pricing:configuracion")


class ConfiguracionPreciosView(UserPassesTestMixin, TemplateView):
    """
    Pantalla central para que Diego configure márgenes: general, mano de
    obra, por categoría (el nivel que más se usa en la práctica), por
    marca, y una búsqueda para llegar al margen de un producto puntual sin
    tener que listar el catálogo entero acá.
    """

    template_name = "pricing/configuracion.html"
    raise_exception = True

    def test_func(self):
        return puede_gestionar_margenes(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        config = ConfiguracionGeneral.obtener()

        context["config_form"] = ConfiguracionGeneralForm(instance=config)
        context["categorias"] = [
            {"categoria": c, "form": MargenCategoriaForm(instance=c)}
            for c in Categoria.objects.all()
        ]
        context["marcas"] = [
            {"marca": m, "form": MargenMarcaForm(instance=m)}
            for m in Marca.objects.all()
        ]

        q = self.request.GET.get("q", "")
        context["q"] = q
        if q:
            productos = Producto.objects.filter(
                db_models.Q(nombre__icontains=q) | db_models.Q(codigo__icontains=q)
            ).select_related("marca")[:25]
            context["resultados_producto"] = [
                {"producto": p, "form": MargenProductoForm(instance=p)} for p in productos
            ]

        return context
