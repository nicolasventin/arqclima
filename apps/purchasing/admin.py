from django.contrib import admin

from .models import LineaOrdenCompra, OrdenDeCompra


class LineaOrdenCompraInline(admin.TabularInline):
    model = LineaOrdenCompra
    extra = 0


@admin.register(OrdenDeCompra)
class OrdenDeCompraAdmin(admin.ModelAdmin):
    list_display = ("numero", "proveedor", "deposito_destino", "estado", "creado_por", "creado_en")
    list_filter = ("estado", "deposito_destino")
    readonly_fields = ("numero",)
    inlines = [LineaOrdenCompraInline]
