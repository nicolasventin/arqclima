from django.contrib import admin

from .models import LineaOrdenCompra, OrdenDeCompra


class LineaOrdenCompraInline(admin.TabularInline):
    model = LineaOrdenCompra
    extra = 0


@admin.register(OrdenDeCompra)
class OrdenDeCompraAdmin(admin.ModelAdmin):
    list_display = (
        "numero",
        "proveedor",
        "deposito_destino",
        "estado",
        "estado_envio",
        "enviada_a",
        "creado_por",
        "creado_en",
    )
    list_filter = ("estado", "estado_envio", "deposito_destino")
    readonly_fields = (
        "numero",
        "estado_envio",
        "enviada_a",
        "ultimo_intento_envio_en",
        "ultimo_error_envio",
        "pdf_generado",
        "enviada_por",
        "enviada_en",
    )
    inlines = [LineaOrdenCompraInline]
