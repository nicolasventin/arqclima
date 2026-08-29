from django.contrib import admin

from .models import (
    ImportacionFila,
    ImportacionImagen,
    ImportacionListaPrecios,
    ProveedorColumnMapping,
)


class ImportacionFilaInline(admin.TabularInline):
    model = ImportacionFila
    extra = 0
    can_delete = False
    fields = (
        "origen",
        "numero_fila",
        "confianza",
        "marca_texto",
        "codigo",
        "nombre_texto",
        "costo",
        "categoria",
        "detalle",
        "incluir",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ImportacionImagenInline(admin.TabularInline):
    model = ImportacionImagen
    extra = 0
    can_delete = False
    fields = (
        "origen",
        "numero_fila_origen",
        "nombre_original",
        "ancho",
        "alto",
        "huella_sha256",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ImportacionListaPrecios)
class ImportacionListaPreciosAdmin(admin.ModelAdmin):
    """
    Solo lectura: las importaciones se cargan y confirman desde su propio
    flujo (vista previa + confirmar). El admin conserva trazabilidad.
    """

    list_display = (
        "id",
        "proveedor",
        "tipo_archivo",
        "estado_analisis",
        "estado",
        "usa_ia",
        "cargado_por",
        "cargado_en",
        "confirmada_por",
    )
    list_filter = ("tipo_archivo", "estado_analisis", "estado", "usa_ia", "proveedor")
    search_fields = ("proveedor__nombre_comercial", "archivo")
    date_hierarchy = "cargado_en"
    readonly_fields = (
        "proveedor",
        "archivo",
        "tipo_archivo",
        "estado_analisis",
        "advertencias_analisis",
        "analizado_en",
        "usa_ia",
        "ia_resultado",
        "cargado_por",
        "cargado_en",
        "estado",
        "confirmada_por",
        "confirmada_en",
    )
    inlines = [ImportacionFilaInline, ImportacionImagenInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProveedorColumnMapping)
class ProveedorColumnMappingAdmin(admin.ModelAdmin):
    """Solo lectura: el cache lo escribe services.py, nunca una persona a mano."""

    list_display = ("id", "proveedor", "encabezados_hash", "creado_en")
    list_filter = ("proveedor",)
    search_fields = ("proveedor__nombre_comercial", "encabezados_hash")
    date_hierarchy = "creado_en"
    readonly_fields = ("proveedor", "encabezados", "encabezados_hash", "mapeo", "creado_en")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
