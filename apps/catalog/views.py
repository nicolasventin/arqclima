from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import models as db_models
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin
from apps.pricing.forms import MargenProductoForm, RegistrarCostoForm
from apps.pricing.permissions import puede_gestionar_margenes, puede_registrar_costo, puede_ver_precio
from apps.pricing.services import calcular_precio_venta, costo_actual, margen_efectivo, proveedor_mas_conveniente

from .forms import ProductoForm, ProductoProveedorForm, ProveedorForm
from .models import Categoria, Marca, Producto, ProductoProveedor, Proveedor
from .permissions import puede_crear_producto, puede_editar_producto


class ProductoListView(PermisoRequeridoMixin, ListView):
    model = Producto
    permission_required = "catalog.view_producto"
    template_name = "catalog/producto_list.html"
    context_object_name = "productos"
    paginate_by = 50

    def get_queryset(self):
        qs = Producto.objects.select_related("marca", "categoria")
        q = self.request.GET.get("q")
        marca_id = self.request.GET.get("marca")
        categoria_id = self.request.GET.get("categoria")
        linea = self.request.GET.get("linea")

        if q:
            qs = qs.filter(db_models.Q(nombre__icontains=q) | db_models.Q(codigo__icontains=q))
        if marca_id:
            qs = qs.filter(marca_id=marca_id)
        if categoria_id:
            qs = qs.filter(categoria_id=categoria_id)
        if linea == "repuestos":
            qs = qs.filter(es_repuesto=True)
        elif linea == "general":
            qs = qs.filter(es_repuesto=False)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["marcas"] = Marca.objects.all()
        context["categorias"] = Categoria.objects.all()
        context["puede_crear"] = puede_crear_producto(self.request.user)
        return context


class ProductoDetailView(PermisoRequeridoMixin, DetailView):
    model = Producto
    permission_required = "catalog.view_producto"
    template_name = "catalog/producto_detail.html"
    context_object_name = "producto"

    def get_queryset(self):
        return Producto.objects.select_related("marca", "categoria").prefetch_related(
            "productoproveedor_set__proveedor"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        puede_editar = puede_editar_producto(self.request.user, self.object)
        context["puede_editar"] = puede_editar

        if puede_editar:
            ya_vinculados = self.object.productoproveedor_set.values_list("proveedor_id", flat=True)
            proveedor_form = ProductoProveedorForm()
            proveedor_form.fields["proveedor"].queryset = Proveedor.objects.filter(
                activo=True
            ).exclude(id__in=ya_vinculados)
            context["proveedor_form"] = proveedor_form
            context["proveedores_disponibles"] = proveedor_form.fields["proveedor"].queryset

        puede_ver_precio_actual = puede_ver_precio(self.request.user, self.object)
        context["puede_ver_precio"] = puede_ver_precio_actual

        if puede_ver_precio_actual:
            mejor_pp, _ = proveedor_mas_conveniente(self.object)
            filas_precio = []
            for pp in self.object.productoproveedor_set.all():
                historial = costo_actual(pp)
                precio_venta = None
                if historial is not None:
                    precio_venta, _ = calcular_precio_venta(self.object, historial.costo)
                filas_precio.append({
                    "relacion": pp,
                    "costo": historial.costo if historial else None,
                    "fecha": historial.vigente_desde if historial else None,
                    "precio_venta": precio_venta,
                    "es_mas_conveniente": mejor_pp is not None and pp.pk == mejor_pp.pk,
                })
            context["filas_precio"] = filas_precio

            margen, origen_margen = margen_efectivo(self.object)
            context["margen_actual"] = margen
            context["margen_origen"] = origen_margen
            context["puede_registrar_costo"] = puede_registrar_costo(self.request.user, self.object)
            context["puede_gestionar_margenes"] = puede_gestionar_margenes(self.request.user)
            context["costo_form"] = RegistrarCostoForm()
            context["margen_form"] = MargenProductoForm(instance=self.object)

        return context


class ProductoCreateView(UserPassesTestMixin, CreateView):
    model = Producto
    form_class = ProductoForm
    template_name = "catalog/producto_form.html"
    raise_exception = True

    def test_func(self):
        return puede_crear_producto(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["forzar_repuesto"] = not self.request.user.has_perm("catalog.change_producto")
        return kwargs

    def form_valid(self, form):
        if not self.request.user.has_perm("catalog.change_producto"):
            form.instance.es_repuesto = True
        response = super().form_valid(form)
        log_action(
            self.request.user, "create_producto", self.object, f"Producto creado: {self.object}"
        )
        return response

    def get_success_url(self):
        return reverse("catalog:producto_detalle", args=[self.object.pk])


class ProductoUpdateView(UserPassesTestMixin, UpdateView):
    model = Producto
    form_class = ProductoForm
    template_name = "catalog/producto_form.html"
    raise_exception = True

    def test_func(self):
        producto = self.get_object()
        self._es_repuesto_antes = producto.es_repuesto
        return puede_editar_producto(self.request.user, producto)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["forzar_repuesto"] = not self.request.user.has_perm("catalog.change_producto")
        return kwargs

    def form_valid(self, form):
        if not self.request.user.has_perm("catalog.change_producto"):
            form.instance.es_repuesto = True
        response = super().form_valid(form)

        detalle = f"Producto editado: {self.object}"
        if getattr(self, "_es_repuesto_antes", False) and not self.object.es_repuesto:
            detalle += (
                " — se le quitó la marca de línea de repuestos "
                "(deja de ser editable para el rol Service y Repuestos)"
            )
        log_action(self.request.user, "update_producto", self.object, detalle)
        return response

    def get_success_url(self):
        return reverse("catalog:producto_detalle", args=[self.object.pk])


class ProductoAgregarProveedorView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.producto = get_object_or_404(Producto, pk=self.kwargs["pk"])
        return puede_editar_producto(self.request.user, self.producto)

    def post(self, request, pk):
        form = ProductoProveedorForm(request.POST)
        if form.is_valid():
            relacion = form.save(commit=False)
            relacion.producto = self.producto
            relacion.save()
            log_action(
                request.user,
                "add_proveedor_producto",
                self.producto,
                f"Proveedor {relacion.proveedor} agregado a {self.producto}",
            )
        return redirect("catalog:producto_detalle", pk=pk)


class ProductoQuitarProveedorView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.relacion = get_object_or_404(
            ProductoProveedor, pk=self.kwargs["relacion_pk"], producto_id=self.kwargs["pk"]
        )
        return puede_editar_producto(self.request.user, self.relacion.producto)

    def post(self, request, pk, relacion_pk):
        producto = self.relacion.producto
        detalle = f"Proveedor {self.relacion.proveedor} quitado de {producto}"
        self.relacion.delete()
        log_action(request.user, "remove_proveedor_producto", producto, detalle)
        return redirect("catalog:producto_detalle", pk=pk)


class ProveedorListView(PermisoRequeridoMixin, ListView):
    model = Proveedor
    permission_required = "catalog.view_proveedor"
    template_name = "catalog/proveedor_list.html"
    context_object_name = "proveedores"


class ProveedorCreateView(PermisoRequeridoMixin, CreateView):
    model = Proveedor
    form_class = ProveedorForm
    permission_required = "catalog.add_proveedor"
    template_name = "catalog/proveedor_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            self.request.user, "create_proveedor", self.object, f"Proveedor creado: {self.object}"
        )
        return response

    def get_success_url(self):
        return reverse("catalog:proveedor_lista")


class ProveedorUpdateView(PermisoRequeridoMixin, UpdateView):
    model = Proveedor
    form_class = ProveedorForm
    permission_required = "catalog.change_proveedor"
    template_name = "catalog/proveedor_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            self.request.user, "update_proveedor", self.object, f"Proveedor editado: {self.object}"
        )
        return response

    def get_success_url(self):
        return reverse("catalog:proveedor_lista")
