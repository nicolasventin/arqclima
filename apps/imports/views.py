from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.services import log_action

from .forms import NuevaImportacionForm
from .models import ImportacionFila, ImportacionListaPrecios
from .parsing import ColumnasNoDetectadas
from .permissions import puede_importar
from .services import confirmar_importacion, procesar_importacion


class ImportacionListView(UserPassesTestMixin, ListView):
    model = ImportacionListaPrecios
    template_name = "imports/lista.html"
    context_object_name = "importaciones"
    raise_exception = True

    def test_func(self):
        return puede_importar(self.request.user)

    def get_queryset(self):
        qs = ImportacionListaPrecios.objects.select_related("proveedor", "cargado_por")
        if not self.request.user.has_perm("pricing.view_historialcosto"):
            qs = qs.filter(cargado_por=self.request.user)
        return qs


class NuevaImportacionView(UserPassesTestMixin, View):
    template_name = "imports/nueva.html"
    raise_exception = True

    def test_func(self):
        return puede_importar(self.request.user)

    def get(self, request):
        return render(request, self.template_name, {"form": NuevaImportacionForm()})

    def post(self, request):
        form = NuevaImportacionForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        importacion = ImportacionListaPrecios.objects.create(
            proveedor=form.cleaned_data["proveedor"],
            archivo=form.cleaned_data["archivo"],
            cargado_por=request.user,
        )

        try:
            procesar_importacion(importacion)
        except ColumnasNoDetectadas as exc:
            importacion.delete()
            form.add_error("archivo", str(exc))
            return render(request, self.template_name, {"form": form})
        except Exception:
            importacion.delete()
            form.add_error(
                "archivo",
                "No se pudo leer el archivo. Verificá que sea un Excel (.xlsx) válido.",
            )
            return render(request, self.template_name, {"form": form})

        cantidad_filas = importacion.filas.count()
        log_action(
            request.user,
            "cargar_lista_precios",
            importacion,
            f"Lista de {importacion.proveedor} cargada: {cantidad_filas} filas",
        )
        return redirect("imports:detalle", pk=importacion.pk)


class ImportacionDetailView(UserPassesTestMixin, DetailView):
    model = ImportacionListaPrecios
    template_name = "imports/detalle.html"
    context_object_name = "importacion"
    raise_exception = True

    def test_func(self):
        if not puede_importar(self.request.user):
            return False
        importacion = self.get_object()
        if self.request.user.has_perm("pricing.view_historialcosto"):
            return True
        return importacion.cargado_por_id == self.request.user.id

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = self.object.filas.select_related("producto").all()
        context["filas"] = filas
        context["resumen"] = {
            etiqueta: filas.filter(categoria=valor).count()
            for valor, etiqueta in ImportacionFila.Categoria.choices
        }
        return context


class ConfirmarImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(ImportacionListaPrecios, pk=self.kwargs["pk"])
        return puede_importar(self.request.user) and self.importacion.estado == (
            ImportacionListaPrecios.Estado.PENDIENTE
        )

    def post(self, request, pk):
        marcadas = set(request.POST.getlist("incluir"))
        for fila in self.importacion.filas.exclude(categoria=ImportacionFila.Categoria.ERROR):
            fila.incluir = str(fila.pk) in marcadas
            fila.save(update_fields=["incluir"])

        contadores = confirmar_importacion(self.importacion, request.user)
        log_action(
            request.user,
            "confirmar_importacion",
            self.importacion,
            f"Importación #{self.importacion.pk} confirmada: "
            f"{contadores['creados']} producto(s) nuevos, "
            f"{contadores['actualizados']} costo(s) actualizados, "
            f"{contadores['omitidos']} omitido(s) por permisos",
        )
        messages.success(
            request,
            f"Importación confirmada: {contadores['creados']} productos nuevos, "
            f"{contadores['actualizados']} costos actualizados.",
        )
        return redirect("imports:detalle", pk=pk)


class DescartarImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(ImportacionListaPrecios, pk=self.kwargs["pk"])
        return puede_importar(self.request.user) and self.importacion.estado == (
            ImportacionListaPrecios.Estado.PENDIENTE
        )

    def post(self, request, pk):
        self.importacion.estado = ImportacionListaPrecios.Estado.DESCARTADA
        self.importacion.save(update_fields=["estado"])
        log_action(
            request.user,
            "descartar_importacion",
            self.importacion,
            f"Importación #{self.importacion.pk} descartada sin aplicar cambios",
        )
        messages.info(request, "Importación descartada.")
        return redirect("imports:lista")
