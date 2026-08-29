from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.services import log_action
from apps.core.mixins import PermisoRequeridoMixin
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.stock.models import Deposito
from apps.stock.permissions import puede_forzar_stock_negativo
from apps.stock.services import StockInsuficienteError, stock_actual

from .forms import (
    ActualizarCantidadMaterialForm,
    AsignarTecnicoForm,
    CancelarTrabajoForm,
    CrearTrabajoForm,
    EtapaTrabajoForm,
    MaterialCatalogoForm,
    MaterialManualForm,
    RegistrarConsumoForm,
)
from .models import ORDEN_ESTADOS, EstadoTrabajo, EtapaTrabajo, MaterialTrabajo, Trabajo
from .permissions import (
    puede_asignar_tecnico,
    puede_cambiar_estado_trabajo,
    puede_cancelar_trabajo,
    puede_crear_trabajo,
    puede_gestionar_materiales,
    puede_registrar_consumo_material,
    queryset_trabajos_visibles,
)
from .services import (
    TransicionInvalidaError,
    cambiar_estado_trabajo,
    cancelar_trabajo,
    cantidad_enviada,
    cantidad_pendiente_envio,
    cantidad_usada_neta,
    crear_trabajo,
    enviar_material,
    enviar_materiales_pendientes,
    generar_listado_materiales,
    materiales_pendientes_de_envio,
    registrar_sobrante,
)


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

        puede_materiales = puede_gestionar_materiales(self.request.user)
        puede_forzar = puede_forzar_stock_negativo(self.request.user)
        context["puede_gestionar_materiales"] = puede_materiales
        context["puede_forzar_stock_negativo"] = puede_forzar
        context["listado_generado"] = trabajo.materiales.exists() or trabajo.etapas.exists()

        def _fila(material):
            pendiente = cantidad_pendiente_envio(material)
            enviado = cantidad_enviada(material)
            neto = cantidad_usada_neta(material)
            disponible = (
                stock_actual(material.producto, Deposito.GENERAL)
                if material.producto_id is not None
                else None
            )
            requiere_forzado = (
                material.producto_id is not None
                and pendiente > 0
                and disponible < pendiente
            )
            return {
                "material": material,
                "pendiente_envio": pendiente,
                "enviado": enviado,
                "neto_enviado": neto,
                "stock_disponible": disponible,
                "requiere_forzado": requiere_forzado,
                "puede_forzar": puede_forzar,
                "puede_enviar": (
                    material.producto_id is not None
                    and pendiente > 0
                    and puede_materiales
                    and not requiere_forzado
                ),
                "puede_consumo": (
                    material.producto_id is not None
                    and neto > 0
                    and puede_registrar_consumo_material(self.request.user, material)
                ),
            }

        etapas = trabajo.etapas.prefetch_related("materiales__producto").all()
        context["etapas"] = [
            {"etapa": etapa, "filas": [_fila(m) for m in etapa.materiales.all()]} for etapa in etapas
        ]
        context["filas_sin_etapa"] = [
            _fila(m)
            for m in trabajo.materiales.filter(etapa__isnull=True).select_related("producto")
        ]

        if puede_materiales:
            context["etapa_form"] = EtapaTrabajoForm()
            context["material_catalogo_form"] = MaterialCatalogoForm(trabajo=trabajo)
            context["material_manual_form"] = MaterialManualForm(trabajo=trabajo)

            pendientes_batch = materiales_pendientes_de_envio(trabajo)
            context["puede_enviar_pendientes"] = bool(pendientes_batch)

            requerido_por_producto = {}
            producto_por_id = {}
            for material in pendientes_batch:
                requerido_por_producto[material.producto_id] = (
                    requerido_por_producto.get(material.producto_id, 0)
                    + cantidad_pendiente_envio(material)
                )
                producto_por_id[material.producto_id] = material.producto

            context["batch_requiere_forzado"] = any(
                stock_actual(producto_por_id[producto_id], Deposito.GENERAL) < requerido
                for producto_id, requerido in requerido_por_producto.items()
            )

        # Advertencia persistente (no bloqueante) mientras el trabajo
        # esté en Listo o más adelante y sigan quedando materiales sin
        # enviar — se recalcula en vivo en cada render, no es un flag
        # guardado: si después alguien termina de enviarlos, deja de
        # mostrarse sola. El registro en AuditLog de que en su momento
        # se marcó Listo con pendientes queda igual, aparte de esto.
        pendientes = materiales_pendientes_de_envio(trabajo)
        context["mostrar_advertencia_pendientes"] = (
            trabajo.estado in ORDEN_ESTADOS
            and ORDEN_ESTADOS.index(trabajo.estado) >= ORDEN_ESTADOS.index(EstadoTrabajo.LISTO)
            and bool(pendientes)
        )
        context["materiales_pendientes_envio"] = pendientes

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


class GenerarListadoMaterialesView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, pk):
        try:
            generar_listado_materiales(self.trabajo, request.user)
            messages.success(request, "Listado de materiales generado.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("jobs:detalle", pk=pk)


class AgregarEtapaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, pk):
        form = EtapaTrabajoForm(request.POST)
        if form.is_valid():
            etapa = form.save(commit=False)
            etapa.trabajo = self.trabajo
            etapa.orden = self.trabajo.etapas.count()
            etapa.save()
        else:
            messages.error(request, "No se pudo agregar la etapa.")
        return redirect("jobs:detalle", pk=pk)


class EliminarEtapaView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.etapa = get_object_or_404(EtapaTrabajo, pk=self.kwargs["etapa_pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, etapa_pk):
        trabajo_pk = self.etapa.trabajo_id
        if self.etapa.materiales.exists():
            messages.error(
                request,
                "No se puede eliminar una etapa que todavía tiene materiales. Movelos o eliminalos primero.",
            )
        else:
            self.etapa.delete()
        return redirect("jobs:detalle", pk=trabajo_pk)


class AgregarMaterialCatalogoView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, pk):
        form = MaterialCatalogoForm(request.POST, trabajo=self.trabajo)
        if form.is_valid():
            material = form.save(commit=False)
            material.trabajo = self.trabajo
            material.orden = self.trabajo.materiales.count()
            material.save()
        else:
            messages.error(request, "No se pudo agregar el material.")
        return redirect("jobs:detalle", pk=pk)


class AgregarMaterialManualView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, pk):
        form = MaterialManualForm(request.POST, trabajo=self.trabajo)
        if form.is_valid():
            material = form.save(commit=False)
            material.trabajo = self.trabajo
            material.orden = self.trabajo.materiales.count()
            material.save()
        else:
            messages.error(request, "No se pudo agregar el material.")
        return redirect("jobs:detalle", pk=pk)


class ActualizarCantidadMaterialView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.material = get_object_or_404(MaterialTrabajo, pk=self.kwargs["material_pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, material_pk):
        form = ActualizarCantidadMaterialForm(request.POST, instance=self.material)
        if form.is_valid():
            form.save()
        return redirect("jobs:detalle", pk=self.material.trabajo_id)


class EliminarMaterialView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.material = get_object_or_404(MaterialTrabajo, pk=self.kwargs["material_pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, material_pk):
        trabajo_pk = self.material.trabajo_id
        self.material.delete()
        return redirect("jobs:detalle", pk=trabajo_pk)


class EnviarMaterialView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.material = get_object_or_404(MaterialTrabajo, pk=self.kwargs["material_pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, material_pk):
        try:
            enviar_material(
                self.material,
                request.user,
                forzar_stock_negativo=request.POST.get("forzar_stock_negativo") == "on",
                motivo_forzado=request.POST.get("motivo_forzado", ""),
            )
            messages.success(request, "Material enviado.")
        except (StockInsuficienteError, PermissionError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("jobs:detalle", pk=self.material.trabajo_id)


class EnviarMaterialesPendientesView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.trabajo = get_object_or_404(Trabajo, pk=self.kwargs["pk"])
        return puede_gestionar_materiales(self.request.user)

    def post(self, request, pk):
        try:
            enviados = enviar_materiales_pendientes(
                self.trabajo,
                request.user,
                forzar_stock_negativo=request.POST.get("forzar_stock_negativo") == "on",
                motivo_forzado=request.POST.get("motivo_forzado", ""),
            )
        except (StockInsuficienteError, PermissionError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            if enviados:
                messages.success(request, f"{len(enviados)} material(es) enviado(s).")
            else:
                messages.info(request, "No había materiales pendientes de envío.")
        return redirect("jobs:detalle", pk=pk)


class RegistrarConsumoMaterialView(UserPassesTestMixin, View):
    template_name = "jobs/registrar_consumo_form.html"
    raise_exception = True

    def test_func(self):
        self.material = get_object_or_404(MaterialTrabajo, pk=self.kwargs["material_pk"])
        return puede_registrar_consumo_material(self.request.user, self.material)

    def get(self, request, material_pk):
        neto = cantidad_usada_neta(self.material)
        form = RegistrarConsumoForm(initial={"cantidad_usada": neto})
        return render(
            request, self.template_name, {"form": form, "material": self.material, "neto": neto}
        )

    def post(self, request, material_pk):
        neto = cantidad_usada_neta(self.material)
        form = RegistrarConsumoForm(request.POST)
        if not form.is_valid():
            return render(
                request, self.template_name, {"form": form, "material": self.material, "neto": neto}
            )

        usada = form.cleaned_data["cantidad_usada"]
        sobrante = neto - usada
        if sobrante > 0:
            try:
                registrar_sobrante(self.material, sobrante, request.user)
                messages.success(request, f"Sobrante registrado: {sobrante} vuelven a stock.")
            except ValueError as exc:
                messages.error(request, str(exc))
        elif sobrante < 0:
            form.add_error("cantidad_usada", f"No puede ser mayor a lo enviado ({neto}).")
            return render(
                request, self.template_name, {"form": form, "material": self.material, "neto": neto}
            )
        else:
            messages.info(request, "Sin sobrante — se usó todo lo enviado.")
        return redirect("jobs:detalle", pk=self.material.trabajo_id)
