import logging

from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.audit.services import log_action
from apps.catalog.forms import ProveedorRapidoForm

from .forms import (
    AsignarMarcaImportacionForm,
    EditarFilaImportacionForm,
    NuevaImportacionForm,
)
from .models import ImportacionFila, ImportacionImagen, ImportacionListaPrecios
from .parsing import (
    ArchivoImportacionInvalido,
    ColumnasNoDetectadas,
    tipo_archivo_por_nombre,
)
from .permissions import puede_importar
from .services import (
    asignar_marca_filas_sin_marca,
    confirmar_importacion,
    procesar_importacion,
    reclasificar_fila,
)

logger = logging.getLogger(__name__)


def _contexto_nueva_importacion(user, form):
    puede_crear_proveedor = user.has_perm("catalog.add_proveedor")
    return {
        "form": form,
        "puede_crear_proveedor": puede_crear_proveedor,
        "proveedor_rapido_form": ProveedorRapidoForm() if puede_crear_proveedor else None,
    }


def _puede_ver_importacion(user, importacion):
    if not puede_importar(user):
        return False
    if user.has_perm("pricing.view_historialcosto"):
        return True
    return importacion.cargado_por_id == user.id


def _borrar_importacion_fallida(importacion):
    for imagen in importacion.imagenes.all():
        if imagen.archivo:
            imagen.archivo.delete(save=False)
    if importacion.archivo:
        importacion.archivo.delete(save=False)
    importacion.delete()


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
        return qs.prefetch_related("filas", "imagenes")


class NuevaImportacionView(UserPassesTestMixin, View):
    template_name = "imports/nueva.html"
    raise_exception = True

    def test_func(self):
        return puede_importar(self.request.user)

    def get(self, request):
        form = NuevaImportacionForm()
        return render(
            request,
            self.template_name,
            _contexto_nueva_importacion(request.user, form),
        )

    def post(self, request):
        form = NuevaImportacionForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                _contexto_nueva_importacion(request.user, form),
            )

        archivo = form.cleaned_data["archivo"]
        tipo_archivo = tipo_archivo_por_nombre(archivo.name)
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=form.cleaned_data["proveedor"],
            archivo=archivo,
            tipo_archivo=tipo_archivo,
            cargado_por=request.user,
        )

        try:
            resultado = procesar_importacion(importacion)
        except (ColumnasNoDetectadas, ArchivoImportacionInvalido) as exc:
            _borrar_importacion_fallida(importacion)
            form.add_error("archivo", str(exc))
            return render(
                request,
                self.template_name,
                _contexto_nueva_importacion(request.user, form),
            )
        except Exception:
            # Este except es deliberadamente genérico (cualquier excepción no
            # prevista no debe tumbar la request con un 500), pero eso mismo
            # disfraza bugs reales de código como si fueran problemas del
            # archivo del usuario — pasó en la práctica con un ValueError de
            # parsing.py sin ninguna relación con el archivo. El traceback
            # completo queda logueado con logger.exception() ANTES del
            # mensaje genérico, para que quede registrado para quien lo
            # audite después; el mensaje al usuario se mantiene genérico
            # a propósito.
            logger.exception(
                "Fallo inesperado al analizar la importación %s (archivo=%r)",
                importacion.pk,
                importacion.archivo.name,
            )
            _borrar_importacion_fallida(importacion)
            form.add_error(
                "archivo",
                "No se pudo analizar el archivo de forma segura. Verificá que no esté "
                "dañado y que sea realmente un Excel, CSV, PDF o Word compatible.",
            )
            return render(
                request,
                self.template_name,
                _contexto_nueva_importacion(request.user, form),
            )

        cantidad_filas = importacion.filas.count()
        cantidad_imagenes = importacion.imagenes.count()
        log_action(
            request.user,
            "cargar_lista_precios",
            importacion,
            (
                f"{importacion.get_tipo_archivo_display()} de {importacion.proveedor}: "
                f"{cantidad_filas} fila(s), {cantidad_imagenes} imagen(es), "
                f"{len(resultado.advertencias)} advertencia(s)."
            ),
        )
        return redirect("imports:detalle", pk=importacion.pk)


class ImportacionDetailView(UserPassesTestMixin, DetailView):
    model = ImportacionListaPrecios
    template_name = "imports/detalle.html"
    context_object_name = "importacion"
    raise_exception = True

    def test_func(self):
        importacion = self.get_object()
        return _puede_ver_importacion(self.request.user, importacion)

    def get_queryset(self):
        return ImportacionListaPrecios.objects.select_related(
            "proveedor",
            "cargado_por",
            "confirmada_por",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filas = self.object.filas.select_related("producto", "producto__marca").all()
        imagenes = self.object.imagenes.all()

        context["filas"] = filas
        context["imagenes"] = imagenes
        context["resumen"] = {
            etiqueta: filas.filter(categoria=valor).count()
            for valor, etiqueta in ImportacionFila.Categoria.choices
        }
        context["resumen_confianza"] = {
            etiqueta: filas.filter(confianza=valor).count()
            for valor, etiqueta in ImportacionFila.Confianza.choices
        }
        context["filas_revisar"] = filas.filter(
            categoria__in=[
                ImportacionFila.Categoria.PARA_REVISAR,
                ImportacionFila.Categoria.ERROR,
            ]
        ).count()
        context["filas_sin_marca"] = filas.filter(marca_texto="").count()
        context["form_marca_masiva"] = AsignarMarcaImportacionForm()
        context["filas_aplicables"] = filas.exclude(
            categoria__in=[
                ImportacionFila.Categoria.PARA_REVISAR,
                ImportacionFila.Categoria.ERROR,
                ImportacionFila.Categoria.SIN_CAMBIOS,
            ]
        ).count()
        context["puede_confirmar"] = (
            self.object.estado == ImportacionListaPrecios.Estado.PENDIENTE
            and filas.exists()
        )
        return context




class AsignarMarcaImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(
            ImportacionListaPrecios,
            pk=self.kwargs["pk"],
        )
        return (
            _puede_ver_importacion(self.request.user, self.importacion)
            and self.importacion.estado == ImportacionListaPrecios.Estado.PENDIENTE
        )

    def post(self, request, pk):
        form = AsignarMarcaImportacionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Seleccioná una marca válida.")
            return redirect("imports:detalle", pk=pk)

        cantidad = asignar_marca_filas_sin_marca(
            self.importacion,
            request.user,
            form.cleaned_data["marca"],
        )
        log_action(
            request.user,
            "asignar_marca_importacion",
            self.importacion,
            (
                f"Marca '{form.cleaned_data['marca']}' asignada a "
                f"{cantidad} fila(s) sin marca."
            ),
        )
        messages.success(
            request,
            (
                f"Marca {form.cleaned_data['marca']} aplicada a "
                f"{cantidad} filas sin marca y preview reclasificado."
            ),
        )
        return redirect("imports:detalle", pk=pk)


class EditarFilaImportacionView(UserPassesTestMixin, View):
    template_name = "imports/fila_editar.html"
    raise_exception = True

    def test_func(self):
        self.fila = get_object_or_404(
            ImportacionFila.objects.select_related("importacion", "importacion__proveedor"),
            pk=self.kwargs["fila_pk"],
            importacion_id=self.kwargs["pk"],
        )
        return (
            _puede_ver_importacion(self.request.user, self.fila.importacion)
            and self.fila.importacion.estado == ImportacionListaPrecios.Estado.PENDIENTE
        )

    def get(self, request, pk, fila_pk):
        return render(
            request,
            self.template_name,
            {
                "importacion": self.fila.importacion,
                "fila": self.fila,
                "form": EditarFilaImportacionForm.desde_fila(self.fila),
            },
        )

    def post(self, request, pk, fila_pk):
        form = EditarFilaImportacionForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "importacion": self.fila.importacion,
                    "fila": self.fila,
                    "form": form,
                },
            )

        categoria_anterior = self.fila.categoria
        reclasificar_fila(
            self.fila,
            request.user,
            form.datos_para_reclasificar(),
        )
        self.fila.refresh_from_db()
        log_action(
            request.user,
            "revisar_fila_importacion",
            self.fila.importacion,
            (
                f"Fila #{self.fila.pk} ({self.fila.origen} / {self.fila.numero_fila}) "
                f"revisada: {categoria_anterior} → {self.fila.categoria}."
            ),
        )
        messages.success(request, "Fila corregida y reclasificada.")
        return redirect("imports:detalle", pk=pk)


class ConfirmarImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(
            ImportacionListaPrecios,
            pk=self.kwargs["pk"],
        )
        return (
            _puede_ver_importacion(self.request.user, self.importacion)
            and self.importacion.estado == ImportacionListaPrecios.Estado.PENDIENTE
        )

    def post(self, request, pk):
        marcadas = set(request.POST.getlist("incluir"))
        filas = self.importacion.filas.all()
        for fila in filas:
            segura = fila.categoria not in (
                ImportacionFila.Categoria.ERROR,
                ImportacionFila.Categoria.PARA_REVISAR,
                ImportacionFila.Categoria.SIN_CAMBIOS,
            )
            fila.incluir = segura and str(fila.pk) in marcadas
            fila.save(update_fields=["incluir"])

        try:
            contadores = confirmar_importacion(self.importacion, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("imports:detalle", pk=pk)

        log_action(
            request.user,
            "confirmar_importacion",
            self.importacion,
            (
                f"Importación #{self.importacion.pk} confirmada: "
                f"{contadores['creados']} producto(s) nuevos, "
                f"{contadores['actualizados']} costo(s) actualizados, "
                f"{contadores['omitidos']} omitido(s)."
            ),
        )
        messages.success(
            request,
            (
                f"Importación confirmada: {contadores['creados']} productos nuevos, "
                f"{contadores['actualizados']} costos actualizados, "
                f"{contadores['omitidos']} omitidos."
            ),
        )
        return redirect("imports:detalle", pk=pk)


class DescartarImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(
            ImportacionListaPrecios,
            pk=self.kwargs["pk"],
        )
        return (
            _puede_ver_importacion(self.request.user, self.importacion)
            and self.importacion.estado == ImportacionListaPrecios.Estado.PENDIENTE
        )

    def post(self, request, pk):
        self.importacion.estado = ImportacionListaPrecios.Estado.DESCARTADA
        self.importacion.save(update_fields=["estado"])
        log_action(
            request.user,
            "descartar_importacion",
            self.importacion,
            f"Importación #{self.importacion.pk} descartada sin aplicar cambios.",
        )
        messages.info(request, "Importación descartada.")
        return redirect("imports:lista")


class ArchivoImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.importacion = get_object_or_404(
            ImportacionListaPrecios,
            pk=self.kwargs["pk"],
        )
        return _puede_ver_importacion(self.request.user, self.importacion)

    def get(self, request, pk):
        try:
            archivo = self.importacion.archivo.open("rb")
        except OSError as exc:
            raise Http404("Archivo no disponible.") from exc
        nombre = self.importacion.archivo.name.rsplit("/", 1)[-1]
        return FileResponse(archivo, as_attachment=False, filename=nombre)


class ImagenImportacionView(UserPassesTestMixin, View):
    raise_exception = True

    def test_func(self):
        self.imagen = get_object_or_404(
            ImportacionImagen.objects.select_related("importacion"),
            pk=self.kwargs["imagen_pk"],
            importacion_id=self.kwargs["pk"],
        )
        return _puede_ver_importacion(self.request.user, self.imagen.importacion)

    def get(self, request, pk, imagen_pk):
        try:
            archivo = self.imagen.archivo.open("rb")
        except OSError as exc:
            raise Http404("Imagen no disponible.") from exc
        return FileResponse(archivo)
