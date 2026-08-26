from django.contrib import admin

from .models import ImportacionFila, ImportacionListaPrecios


class ImportacionFilaInline(admin.TabularInline):
    model = ImportacionFila
    extra = 0
    can_delete = False
    fields = (
        "numero_fila", "marca_texto", "codigo", "nombre_texto", "costo",
        "categoria", "detalle", "incluir",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ImportacionListaPrecios)
class ImportacionListaPreciosAdmin(admin.ModelAdmin):
    """
    Solo lectura: las importaciones se cargan y confirman desde su propio
    flujo (vista previa + confirmar), no desde acá. Esta pantalla es para
    que Diego pueda auditar qué se importó y cuándo.
    """

    list_display = ("id", "proveedor", "estado", "cargado_por", "cargado_en", "confirmada_por")
    list_filter = ("estado", "proveedor")
    search_fields = ("proveedor__nombre_comercial",)
    date_hierarchy = "cargado_en"
    inlines = [ImportacionFilaInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
