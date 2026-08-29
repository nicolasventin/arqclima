from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from apps.catalog.models import ProductoProveedor
from apps.core.mixins import PermisoRequeridoMixin
from apps.pricing.services import costo_actual
from apps.stock.permissions import puede_registrar_entrada_salida

from .forms import CrearOrdenForm, LineaOrdenCompraForm, RecibirLineaForm
from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra
from .permissions import (
    puede_aprobar_orden,
    puede_cancelar_orden,
    puede_cerrar_orden,
    puede_gestionar_orden,
)
from .services import (
    TransicionInvalidaError,
    cambiar_estado_orden,
    cantidad_pendiente_recepcion,
    cantidad_recibida,
    cerrar_orden,
    crear_orden,
    lineas_pendientes_recepcion,
    orden_tiene_recepciones,
    recibir_linea,
)


class OrdenListView(PermisoRequeridoMixin, ListView):
    model = OrdenDeCompra
    permission_required = "purchasing.view_ordendecompra"
    template_name = "purchasing/orden_list.html"
    context_object_name = "ordenes"
    paginate_by = 50

    def get_queryset(self):
        qs = OrdenDeCompra.objects.select_related("proveedor")
        estado = self.request.GET.get("estado")
        q = (self.request.GET.get("q") or "").strip()
        if estado:
            qs = qs.filter(estado=estado)
        if q:
            qs = qs.filter(
                Q(proveedor__nombre_comercial__icontains=q)
                | Q(notas__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estados"] = EstadoOrdenCompra.choices
        return context


class OrdenDetailView(PermisoRequeridoMixin, DetailView):
    model = OrdenDeCompra
    permission_required = "purchasing.view_ordendecompra"
    template_name = "purchasing/orden_detail.html"
    context_object_name = "orden"

    def get_queryset(self):
        return OrdenDeCompra.objects.select_related(
            "proveedor",
            "creado_por",
            "solicitud_aprobacion_por",
            "aprobada_por",
            "rechazada_por",
            "enviada_por",
            "cerrada_por",
            "cancelada_por",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        orden = self.object
        user = self.request.user

        es_borrador = orden.estado == EstadoOrdenCompra.BORRADOR
        tiene_lineas = orden.lineas.exists()
        tiene_recepciones = orden_tiene_recepciones(orden)
        puede_recibir_deposito = puede_registrar_entrada_salida(
            user, orden.deposito_destino
        )

        estados_recepcion = (
            EstadoOrdenCompra.ENVIADA,
            EstadoOrdenCompra.RECEPCION_PARCIAL,
        )

        filas = []
        for linea in orden.lineas.select_related(
            "producto_proveedor__producto",
            "producto_proveedor__proveedor",
        ):
            recibido = cantidad_recibida(linea)
            pendiente = cantidad_pendiente_recepcion(linea)
            filas.append(
                {
                    "linea": linea,
                    "recibido": recibido,
                    "pendiente": pendiente,
                    "puede_recibir": (
                        puede_recibir_deposito
                        and orden.estado in estados_recepcion
                        and pendiente > 0
                    ),
                }
            )
        context["filas"] = filas

        context["puede_gestionar"] = puede_gestionar_orden(user)
        context["puede_editar_lineas"] = es_borrador and puede_gestionar_orden(user)
        context["puede_aprobar"] = (
            puede_aprobar_orden(user)
            and orden.estado == EstadoOrdenCompra.PENDIENTE_APROBACION
        )
        context["puede_enviar_a_aprobacion"] = (
            puede_gestionar_orden(user) and es_borrador and tiene_lineas
        )
        context["puede_reabrir"] = (
            puede_gestionar_orden(user)
            and orden.estado
            in (
                EstadoOrdenCompra.PENDIENTE_APROBACION,
                EstadoOrdenCompra.RECHAZADA,
            )
        )
        context["puede_marcar_enviada"] = (
            puede_gestionar_orden(user)
            and orden.estado == EstadoOrdenCompra.APROBADA
        )
        context["puede_cancelar"] = (
            puede_cancelar_orden(user)
            and orden.estado
            in (
                EstadoOrdenCompra.PENDIENTE_APROBACION,
                EstadoOrdenCompra.APROBADA,
                EstadoOrdenCompra.ENVIADA,
            )
            and not tiene_recepciones
        )
        context["puede_cerrar"] = (
            puede_cerrar_orden(user)
            and orden.estado
            in (
                EstadoOrdenCompra.RECEPCION_PARCIAL,
                EstadoOrdenCompra.RECIBIDA,
            )
        )
        context["cierre_requiere_motivo"] = (
            orden.estado == EstadoOrdenCompra.RECEPCION_PARCIAL
        )
        context["lineas_pendientes"] = (
            lineas_pendientes_recepcion(orden)
            if orden.estado
            in (
                EstadoOrdenCompra.RECEPCION_PARCIAL,
                EstadoOrdenCompra.RECIBIDA,
                EstadoOrdenCompra.CERRADA,
            )
            else []
        )
        context["tiene_lineas"] = tiene_lineas

        if context["puede_editar_lineas"]:
            context["linea_form"] = LineaOrdenCompraForm(orden=orden)

        return context


class CrearOrdenView(UserPassesTestMixin, View):
    template_name = "purchasing/orden_form.html"
    raise_exception = True

    def test_func(self):
        return puede_gestionar_orden(self.request.user)

    def get(self, request):
        return render(request, self.template_name, {"form": CrearOrdenForm()})

    def post(self, request):
        form = CrearOrdenForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        orden = crear_orden(
            proveedor=form.cleaned_data["proveedor"],
            deposito_destino=form.cleaned_data["deposito_destino"],
            usuario=request.user,
            notas=form.cleaned_data["notas"],
        )
        messages.success(request, f"Orden #{orden.numero} creada.")
        return redirect("purchasing:detalle", pk=orden.pk)


class AgregarLineaView(UserPassesTestMixin, View):
    template_name = "purchasing/linea_form.html"
    raise_exception = True

    def test_func(self):
        self.orden = get_object_or_404(OrdenDeCompra, pk=self.kwargs["pk"])
        return (
            puede_gestionar_orden(self.request.user)
            and self.orden.estado == EstadoOrdenCompra.BORRADOR
        )

    def get(self, request, pk):
        initial = {}
        producto_proveedor_id = request.GET.get("producto_proveedor")
        if producto_proveedor_id:
            initial["producto_proveedor"] = producto_proveedor_id
            pp = ProductoProveedor.objects.filter(pk=producto_proveedor_id).first()
            if pp is not None:
                historial = costo_actual(pp)
                if historial is not None:
                    initial["costo_esperado"] = historial.costo
        form = LineaOrdenCompraForm(initial=initial, orden=self.orden)
        return render(
            request,
            self.template_name,
            {"form": form, "orden": self.orden},
        )

    def post(self, request, pk):
        form = LineaOrdenCompraForm(request.POST, orden=self.orden)
        if "recalcular" in request.POST and form.data.get("producto_proveedor"):
            return redirect(
                f"{reverse('purchasing:agregar_linea', args=[pk])}"
                f"?producto_proveedor={form.data.get('producto_proveedor')}"
            )
        if form.is_valid():
            linea = form.save(commit=False)
            linea.orden = self.orden
            linea.save()
            messages.success(request, "Línea agregada.")
        else:
            messages.error(request, "No se pudo agregar la línea.")
            return render(
                request,
                self.template_name,
                {"form": form, "orden": self.orden},
            )
        return redirect("purchasing:detalle", pk=pk)


class EliminarLineaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.linea = get_object_or_404(
            LineaOrdenCompra, pk=self.kwargs["linea_pk"]
        )
        return (
            puede_gestionar_orden(self.request.user)
            and self.linea.orden.estado == EstadoOrdenCompra.BORRADOR
        )

    def post(self, request, linea_pk):
        orden_pk = self.linea.orden_id
        self.linea.delete()
        return redirect("purchasing:detalle", pk=orden_pk)


class _TransicionOrdenView(UserPassesTestMixin, View):
    nuevo_estado = None
    raise_exception = True
    permiso_check = staticmethod(puede_gestionar_orden)

    def test_func(self):
        self.orden = get_object_or_404(
            OrdenDeCompra, pk=self.kwargs["pk"]
        )
        return self.permiso_check(self.request.user)

    def post(self, request, pk):
        try:
            cambiar_estado_orden(
                self.orden,
                self.nuevo_estado,
                request.user,
                motivo=request.POST.get("motivo", ""),
            )
        except (TransicionInvalidaError, ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Orden #{self.orden.numero}: {self.orden.get_estado_display()}.",
            )
        return redirect("purchasing:detalle", pk=pk)


class EnviarAAprobacionView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.PENDIENTE_APROBACION


class ReabrirOrdenView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.BORRADOR


class MarcarEnviadaView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.ENVIADA


class AprobarOrdenView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.APROBADA
    permiso_check = staticmethod(puede_aprobar_orden)


class RechazarOrdenView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.RECHAZADA
    permiso_check = staticmethod(puede_aprobar_orden)


class CancelarOrdenView(_TransicionOrdenView):
    nuevo_estado = EstadoOrdenCompra.CANCELADA
    permiso_check = staticmethod(puede_cancelar_orden)


class CerrarOrdenView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.orden = get_object_or_404(
            OrdenDeCompra, pk=self.kwargs["pk"]
        )
        return puede_cerrar_orden(self.request.user)

    def post(self, request, pk):
        try:
            cerrar_orden(
                self.orden,
                request.user,
                motivo=request.POST.get("motivo", ""),
            )
        except (TransicionInvalidaError, ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Orden #{self.orden.numero} cerrada.")
        return redirect("purchasing:detalle", pk=pk)


class RecibirLineaView(UserPassesTestMixin, View):
    template_name = "purchasing/recibir_form.html"
    raise_exception = True

    def test_func(self):
        self.linea = get_object_or_404(
            LineaOrdenCompra.objects.select_related("orden"),
            pk=self.kwargs["linea_pk"],
        )
        return (
            puede_registrar_entrada_salida(
                self.request.user,
                self.linea.orden.deposito_destino,
            )
            and self.linea.orden.estado
            in (
                EstadoOrdenCompra.ENVIADA,
                EstadoOrdenCompra.RECEPCION_PARCIAL,
            )
        )

    def get(self, request, linea_pk):
        pendiente = cantidad_pendiente_recepcion(self.linea)
        form = RecibirLineaForm(
            initial={
                "cantidad": pendiente,
                "costo_real": self.linea.costo_esperado,
            }
        )
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "linea": self.linea,
                "pendiente": pendiente,
            },
        )

    def post(self, request, linea_pk):
        pendiente = cantidad_pendiente_recepcion(self.linea)
        form = RecibirLineaForm(request.POST)
        if form.is_valid():
            cantidad = form.cleaned_data["cantidad"]
            if cantidad > pendiente:
                form.add_error(
                    "cantidad",
                    f"No puede superar lo pendiente ({pendiente}).",
                )
            else:
                try:
                    recibir_linea(
                        self.linea,
                        cantidad,
                        form.cleaned_data["costo_real"],
                        request.user,
                    )
                    messages.success(request, "Recepción registrada.")
                    return redirect(
                        "purchasing:detalle",
                        pk=self.linea.orden_id,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "linea": self.linea,
                "pendiente": pendiente,
            },
        )
