from django.contrib import admin

from .models import ConfiguracionGeneral, HistorialCosto


@admin.register(ConfiguracionGeneral)
class ConfiguracionGeneralAdmin(admin.ModelAdmin):
    list_display = ("margen_general", "flete_pct", "costo_financiero_pct", "margen_minimo_alerta")

    def has_add_permission(self, request):
        # Es un singleton: si ya existe la fila, no se permite crear otra.
        return not ConfiguracionGeneral.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HistorialCosto)
class HistorialCostoAdmin(admin.ModelAdmin):
    """
    Solo lectura a propósito: el historial de costos es append-only (ver
    docstring del modelo). Esta pantalla es para que Diego pueda auditar,
    no para editar — y aunque alguien lo intentara acá, el trigger de
    Postgres lo rechazaría igual.
    """

    list_display = ("producto_proveedor", "costo", "vigente_desde", "cargado_por", "origen")
    list_filter = ("origen",)
    search_fields = (
        "producto_proveedor__producto__nombre",
        "producto_proveedor__producto__codigo",
    )
    date_hierarchy = "vigente_desde"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
