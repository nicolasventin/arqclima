from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.catalog.models import Producto
from apps.core.mixins import PermisoRequeridoMixin

from .forms import AjusteForm, DevolucionForm, EntradaSalidaForm, SalidaRepuestosForm, StockMinimoForm
from .models import Deposito, MovimientoStock, TipoMovimiento
from .permissions import puede_ajustar_stock, puede_configurar_stock_minimo, puede_registrar_entrada_salida
from .services import (
    bajo_minimo,
    cantidad_pendiente_devolucion,
    registrar_devolucion,
    registrar_movimiento,
    salidas_repuestos_pendientes,
    stock_actual,
)


class StockListView(PermisoRequeridoMixin, ListView):
    """Stock actual por producto — la pantalla de "ver stock" abierta a todos los roles."""

    model = Producto
    permission_required = "stock.view_movimientostock"
    template_name = "stock/stock_list.html"
    context_object_name = "productos"
    paginate_by = 50

    def get_queryset(self):
        qs = Producto.objects.filter(activo=True).select_related("marca")
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(codigo__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = []
        for producto in context["productos"]:
            general = stock_actual(producto, Deposito.GENERAL)
            repuestos = stock_actual(producto, Deposito.REPUESTOS) if producto.es_repuesto else None
            filas.append(
                {
                    "producto": producto,
                    "stock_general": general,
                    "alerta_general": bajo_minimo(producto, Deposito.GENERAL, general),
                    "stock_repuestos": repuestos,
                    "alerta_repuestos": (
                        producto.es_repuesto and bajo_minimo(producto, Deposito.REPUESTOS, repuestos)
                    ),
                }
            )
        context["filas"] = filas
        return context


class MovimientoListView(PermisoRequeridoMixin, ListView):
    model = MovimientoStock
    permission_required = "stock.view_movimientostock"
    template_name = "stock/movimiento_list.html"
    context_object_name = "movimientos"
    paginate_by = 50

    def get_queryset(self):
        qs = MovimientoStock.objects.select_related("producto", "registrado_por")
        deposito = self.request.GET.get("deposito")
        if deposito:
            qs = qs.filter(deposito=deposito)
        return qs


class RegistrarEntradaView(UserPassesTestMixin, View):
    template_name = "stock/movimiento_form.html"
    raise_exception = True

    def test_func(self):
        self.deposito = self.kwargs["deposito"]
        return puede_registrar_entrada_salida(self.request.user, self.deposito)

    def get(self, request, deposito):
        form = EntradaSalidaForm(solo_repuestos=deposito == Deposito.REPUESTOS)
        return render(request, self.template_name, {"form": form, "titulo": "Registrar entrada"})

    def post(self, request, deposito):
        form = EntradaSalidaForm(request.POST, solo_repuestos=deposito == Deposito.REPUESTOS)
        if form.is_valid():
            registrar_movimiento(
                producto=form.cleaned_data["producto"],
                deposito=deposito,
                tipo=TipoMovimiento.ENTRADA,
                cantidad=form.cleaned_data["cantidad"],
                usuario=request.user,
                referencia_libre=form.cleaned_data["referencia_libre"],
            )
            messages.success(request, "Entrada registrada.")
            return redirect("stock:lista")
        return render(request, self.template_name, {"form": form, "titulo": "Registrar entrada"})


class RegistrarSalidaView(UserPassesTestMixin, View):
    template_name = "stock/movimiento_form.html"
    raise_exception = True

    def test_func(self):
        self.deposito = self.kwargs["deposito"]
        return puede_registrar_entrada_salida(self.request.user, self.deposito)

    def _form_class(self):
        return SalidaRepuestosForm if self.deposito == Deposito.REPUESTOS else EntradaSalidaForm

    def get(self, request, deposito):
        form = self._form_class()(solo_repuestos=deposito == Deposito.REPUESTOS)
        return render(request, self.template_name, {"form": form, "titulo": "Registrar salida"})

    def post(self, request, deposito):
        form = self._form_class()(request.POST, solo_repuestos=deposito == Deposito.REPUESTOS)
        if form.is_valid():
            registrar_movimiento(
                producto=form.cleaned_data["producto"],
                deposito=deposito,
                tipo=TipoMovimiento.SALIDA,
                cantidad=-form.cleaned_data["cantidad"],
                usuario=request.user,
                requiere_devolucion=form.cleaned_data.get("requiere_devolucion", False),
                referencia_libre=form.cleaned_data["referencia_libre"],
            )
            messages.success(request, "Salida registrada.")
            return redirect("stock:lista")
        return render(request, self.template_name, {"form": form, "titulo": "Registrar salida"})


class RegistrarAjusteView(UserPassesTestMixin, View):
    template_name = "stock/ajuste_form.html"
    raise_exception = True

    def test_func(self):
        return puede_ajustar_stock(self.request.user, Deposito.GENERAL)

    def get(self, request):
        return render(request, self.template_name, {"form": AjusteForm()})

    def post(self, request):
        form = AjusteForm(request.POST)
        if form.is_valid():
            registrar_movimiento(
                producto=form.cleaned_data["producto"],
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.AJUSTE,
                cantidad=form.cleaned_data["cantidad"],
                usuario=request.user,
                referencia_libre=form.cleaned_data["referencia_libre"],
            )
            messages.success(request, "Ajuste registrado.")
            return redirect("stock:lista")
        return render(request, self.template_name, {"form": form})


class PendientesDevolucionView(UserPassesTestMixin, ListView):
    template_name = "stock/pendientes_devolucion.html"
    context_object_name = "pendientes"
    raise_exception = True

    def test_func(self):
        return puede_registrar_entrada_salida(self.request.user, Deposito.REPUESTOS)

    def get_queryset(self):
        pendientes = salidas_repuestos_pendientes()
        for salida in pendientes:
            salida.enviado = abs(salida.cantidad)
            salida.pendiente = cantidad_pendiente_devolucion(salida)
        return pendientes


class RegistrarDevolucionView(UserPassesTestMixin, View):
    template_name = "stock/devolucion_form.html"
    raise_exception = True

    def test_func(self):
        self.salida = get_object_or_404(
            MovimientoStock, pk=self.kwargs["salida_pk"], tipo=TipoMovimiento.SALIDA,
            deposito=Deposito.REPUESTOS, requiere_devolucion=True,
        )
        return puede_registrar_entrada_salida(self.request.user, Deposito.REPUESTOS)

    def get(self, request, salida_pk):
        pendiente = cantidad_pendiente_devolucion(self.salida)
        return render(
            request, self.template_name,
            {"form": DevolucionForm(), "salida": self.salida, "pendiente": pendiente},
        )

    def post(self, request, salida_pk):
        form = DevolucionForm(request.POST)
        if form.is_valid():
            cantidad = form.cleaned_data["cantidad"]
            try:
                registrar_devolucion(self.salida, cantidad, request.user)
            except ValueError as exc:
                form.add_error("cantidad", str(exc))
            else:
                messages.success(request, "Devolución registrada.")
                return redirect("stock:pendientes_devolucion")

        pendiente = cantidad_pendiente_devolucion(self.salida)
        return render(
            request, self.template_name,
            {"form": form, "salida": self.salida, "pendiente": pendiente},
        )


class ActualizarStockMinimoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        return puede_configurar_stock_minimo(self.request.user)

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        form = StockMinimoForm(request.POST, instance=producto)
        if form.is_valid():
            form.save()
            messages.success(request, "Stock mínimo actualizado.")
        return redirect("catalog:producto_detalle", pk=pk)
