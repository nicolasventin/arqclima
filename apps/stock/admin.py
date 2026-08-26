from django.contrib import admin

from .models import MovimientoStock


@admin.register(MovimientoStock)
class MovimientoStockAdmin(admin.ModelAdmin):
    list_display = ("producto", "deposito", "tipo", "cantidad", "requiere_devolucion", "creado_en")
    list_filter = ("deposito", "tipo", "requiere_devolucion")
    search_fields = ("producto__nombre", "producto__codigo", "referencia_libre")
