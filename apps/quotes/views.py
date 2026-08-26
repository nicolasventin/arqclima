from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView
from xhtml2pdf import pisa

from apps.audit.services import log_action
from apps.catalog.models import ProductoProveedor
from apps.core.mixins import PermisoRequeridoMixin
from apps.pricing.models import ConfiguracionGeneral
from apps.pricing.services import calcular_precio_venta, costo_actual

from .forms import ItemCatalogoForm, ItemManualForm, PresupuestoForm, SeccionPresupuestoForm
from .models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, SeccionPresupuesto
from .permissions import puede_revertir_aceptado
from .services import (
    TransicionInvalidaError,
    calcular_totales,
    cambiar_estado,
    duplicar_presupuesto,
    enviar_presupuesto,
    margen_item,
    sugerir_costo_mano_obra,
)


class PresupuestoListView(PermisoRequeridoMixin, ListView):
    model = Presupuesto
    permission_required = "quotes.view_presupuesto"
    template_name = "quotes/presupuesto_list.html"
    context_object_name = "presupuestos"
    paginate_by = 50

    def get_queryset(self):
        qs = Presupuesto.objects.select_related("cliente")
        estado = self.request.GET.get("estado")
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estados"] = EstadoPresupuesto.choices
        for presupuesto in context["presupuestos"]:
            presupuesto.total_calculado = calcular_totales(presupuesto)["total_final"]
        return context


class PresupuestoCreateView(PermisoRequeridoMixin, CreateView):
    model = Presupuesto
    form_class = PresupuestoForm
    permission_required = "quotes.add_presupuesto"
    template_name = "quotes/presupuesto_form.html"

    def form_valid(self, form):
        form.instance.creado_por = self.request.user
        plantilla = form.cleaned_data.get("plantilla_condiciones")
        form.instance.condiciones = plantilla.texto if plantilla else ""
        response = super().form_valid(form)
        log_action(
            self.request.user, "create_presupuesto", self.object, f"Presupuesto creado: {self.object}"
        )
        return response

    def get_success_url(self):
        return reverse("quotes:detalle", args=[self.object.pk])


class PresupuestoDetailView(PermisoRequeridoMixin, DetailView):
    model = Presupuesto
    permission_required = "quotes.view_presupuesto"
    template_name = "quotes/presupuesto_detail.html"
    context_object_name = "presupuesto"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        presupuesto = self.object
        config = ConfiguracionGeneral.obtener()

        filas = []
        for item in presupuesto.items.select_related("producto", "producto__marca", "seccion").order_by(
            "orden"
        ):
            margen = margen_item(item)
            filas.append(
                {
                    "item": item,
                    "margen": margen,
                    "bajo_margen": margen is not None and margen < config.margen_minimo_alerta,
                }
            )

        secciones = [
            {
                "seccion": seccion,
                "filas": [f for f in filas if f["item"].seccion_id == seccion.id],
            }
            for seccion in presupuesto.secciones.all()
        ]
        filas_sin_seccion = [f for f in filas if f["item"].seccion_id is None]

        es_borrador = presupuesto.estado == EstadoPresupuesto.BORRADOR
        context.update(
            {
                "totales": calcular_totales(presupuesto),
                "secciones": secciones,
                "filas_sin_seccion": filas_sin_seccion,
                "es_borrador": es_borrador,
                "puede_editar_estructura": es_borrador
                and self.request.user.has_perm("quotes.change_presupuesto"),
                "puede_revertir": puede_revertir_aceptado(self.request.user)
                and presupuesto.estado == EstadoPresupuesto.ACEPTADO,
                "seccion_form": SeccionPresupuestoForm(),
                "margen_minimo_alerta": config.margen_minimo_alerta,
            }
        )
        return context


class PresupuestoPDFView(PermisoRequeridoMixin, DetailView):
    model = Presupuesto
    permission_required = "quotes.view_presupuesto"

    def get(self, request, *args, **kwargs):
        presupuesto = self.get_object()
        html = render_to_string(
            "quotes/presupuesto_pdf.html",
            {
                "presupuesto": presupuesto,
                "totales": calcular_totales(presupuesto),
                "secciones": presupuesto.secciones.prefetch_related("items"),
                "items_sin_seccion": presupuesto.items.filter(seccion__isnull=True),
            },
        )
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="presupuesto_{presupuesto.numero}.pdf"'
        pisa.CreatePDF(html, dest=response)
        return response


class DuplicarPresupuestoView(PermisoRequeridoMixin, View):
    permission_required = "quotes.add_presupuesto"

    def post(self, request, pk):
        original = get_object_or_404(Presupuesto, pk=pk)
        nuevo = duplicar_presupuesto(original, request.user)
        messages.success(
            request, f"Se creó el presupuesto #{nuevo.numero} como copia de #{original.numero}."
        )
        return redirect("quotes:detalle", pk=nuevo.pk)


class _TransicionPresupuestoView(UserPassesTestMixin, View):
    nuevo_estado = None
    accion = None
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return self.request.user.has_perm("quotes.change_presupuesto")

    def post(self, request, pk):
        try:
            cambiar_estado(self.presupuesto, self.nuevo_estado, request.user, accion=self.accion)
            messages.success(
                request,
                f"Presupuesto #{self.presupuesto.numero}: {self.presupuesto.get_estado_display()}.",
            )
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        return redirect("quotes:detalle", pk=pk)


class EnviarPresupuestoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return self.request.user.has_perm("quotes.change_presupuesto")

    def post(self, request, pk):
        try:
            enviar_presupuesto(self.presupuesto, request.user)
            messages.success(request, f"Presupuesto #{self.presupuesto.numero} enviado.")
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        return redirect("quotes:detalle", pk=pk)


class AceptarPresupuestoView(_TransicionPresupuestoView):
    nuevo_estado = EstadoPresupuesto.ACEPTADO
    accion = "aceptar_presupuesto"


class RechazarPresupuestoView(_TransicionPresupuestoView):
    nuevo_estado = EstadoPresupuesto.RECHAZADO
    accion = "rechazar_presupuesto"


class CancelarPresupuestoView(_TransicionPresupuestoView):
    """
    Cancela un Borrador o un Enviado (los únicos casos en que este botón
    se muestra). Cancelar un Aceptado es una transición distinta a
    propósito: pasa exclusivamente por RevertirAceptadoView, que exige
    el permiso quotes.revert_presupuesto_aceptado (solo Administrador).
    Sin este chequeo, cambiar_estado() aceptaría igual la transición
    Aceptado→Cancelado (está en TRANSICIONES_VALIDAS) y cualquiera con
    change_presupuesto —Rodrigo incluido— podría revertir un Aceptado
    pegándole directo a esta URL, esquivando el permiso especial.
    """

    nuevo_estado = EstadoPresupuesto.CANCELADO
    accion = "cancelar_presupuesto"

    def test_func(self):
        return super().test_func() and self.presupuesto.estado != EstadoPresupuesto.ACEPTADO


class ReabrirPresupuestoView(_TransicionPresupuestoView):
    nuevo_estado = EstadoPresupuesto.BORRADOR
    accion = "reabrir_presupuesto"


class RevertirAceptadoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return puede_revertir_aceptado(self.request.user)

    def post(self, request, pk):
        try:
            cambiar_estado(
                self.presupuesto,
                EstadoPresupuesto.CANCELADO,
                request.user,
                accion="revertir_presupuesto_aceptado",
                detalle="Aceptado revertido a Cancelado por administrador.",
            )
            messages.success(request, f"Presupuesto #{self.presupuesto.numero} revertido a Cancelado.")
        except TransicionInvalidaError as exc:
            messages.error(request, str(exc))
        return redirect("quotes:detalle", pk=pk)


class AgregarSeccionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return (
            self.request.user.has_perm("quotes.add_seccionpresupuesto")
            and self.presupuesto.estado == EstadoPresupuesto.BORRADOR
        )

    def post(self, request, pk):
        form = SeccionPresupuestoForm(request.POST)
        if form.is_valid():
            seccion = form.save(commit=False)
            seccion.presupuesto = self.presupuesto
            seccion.orden = self.presupuesto.secciones.count()
            seccion.save()
        else:
            messages.error(request, "No se pudo agregar la sección.")
        return redirect("quotes:detalle", pk=pk)


class EliminarSeccionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.seccion = get_object_or_404(SeccionPresupuesto, pk=self.kwargs["seccion_pk"])
        return (
            self.request.user.has_perm("quotes.delete_seccionpresupuesto")
            and self.seccion.presupuesto.estado == EstadoPresupuesto.BORRADOR
        )

    def post(self, request, seccion_pk):
        presupuesto_pk = self.seccion.presupuesto_id
        if self.seccion.items.exists():
            messages.error(
                request,
                "No se puede eliminar una sección que todavía tiene ítems. Movelos o eliminalos primero.",
            )
        else:
            self.seccion.delete()
        return redirect("quotes:detalle", pk=presupuesto_pk)


class AgregarItemCatalogoView(UserPassesTestMixin, View):
    template_name = "quotes/item_catalogo_form.html"
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return (
            self.request.user.has_perm("quotes.add_itempresupuesto")
            and self.presupuesto.estado == EstadoPresupuesto.BORRADOR
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
                    precio, _ = calcular_precio_venta(pp.producto, historial.costo)
                    initial["precio_unitario"] = precio
                    initial["costo_unitario"] = historial.costo
        form = ItemCatalogoForm(initial=initial, presupuesto=self.presupuesto)
        return render(
            request, self.template_name, {"presupuesto": self.presupuesto, "form": form}
        )

    def post(self, request, pk):
        form = ItemCatalogoForm(request.POST, presupuesto=self.presupuesto)
        if "recalcular" in request.POST and form.data.get("producto_proveedor"):
            return redirect(
                f"{reverse('quotes:agregar_item_catalogo', args=[pk])}"
                f"?producto_proveedor={form.data.get('producto_proveedor')}"
            )
        if form.is_valid():
            item = form.save(commit=False)
            item.presupuesto = self.presupuesto
            item.producto = item.producto_proveedor.producto
            item.orden = self.presupuesto.items.count()
            item.save()
            log_action(
                request.user, "agregar_item_presupuesto", self.presupuesto, f"Ítem agregado: {item}"
            )
            return redirect("quotes:detalle", pk=pk)
        return render(
            request, self.template_name, {"presupuesto": self.presupuesto, "form": form}
        )


class AgregarItemManualView(UserPassesTestMixin, View):
    template_name = "quotes/item_manual_form.html"
    raise_exception = True

    def test_func(self):
        self.presupuesto = get_object_or_404(Presupuesto, pk=self.kwargs["pk"])
        return (
            self.request.user.has_perm("quotes.add_itempresupuesto")
            and self.presupuesto.estado == EstadoPresupuesto.BORRADOR
        )

    def get(self, request, pk):
        form = ItemManualForm(presupuesto=self.presupuesto)
        return render(
            request, self.template_name, {"presupuesto": self.presupuesto, "form": form}
        )

    def post(self, request, pk):
        if "sugerir_costo" in request.POST:
            datos = request.POST.dict()
            try:
                precio = Decimal(datos.get("precio_unitario") or "")
                datos["costo_unitario"] = str(sugerir_costo_mano_obra(precio))
            except (InvalidOperation, TypeError):
                messages.error(request, "Cargá primero un precio unitario válido para poder sugerir el costo.")
            form = ItemManualForm(initial=datos, presupuesto=self.presupuesto)
            return render(
                request, self.template_name, {"presupuesto": self.presupuesto, "form": form}
            )

        form = ItemManualForm(request.POST, presupuesto=self.presupuesto)
        if form.is_valid():
            item = form.save(commit=False)
            item.presupuesto = self.presupuesto
            item.orden = self.presupuesto.items.count()
            item.save()
            log_action(
                request.user, "agregar_item_presupuesto", self.presupuesto, f"Ítem agregado: {item}"
            )
            return redirect("quotes:detalle", pk=pk)
        return render(
            request, self.template_name, {"presupuesto": self.presupuesto, "form": form}
        )


class EliminarItemView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.item = get_object_or_404(ItemPresupuesto, pk=self.kwargs["item_pk"])
        return (
            self.request.user.has_perm("quotes.delete_itempresupuesto")
            and self.item.presupuesto.estado == EstadoPresupuesto.BORRADOR
        )

    def post(self, request, item_pk):
        presupuesto_pk = self.item.presupuesto_id
        self.item.delete()
        return redirect("quotes:detalle", pk=presupuesto_pk)
