from django.contrib import admin

from .models import ItemPresupuesto, PlantillaCondiciones, Presupuesto, SeccionPresupuesto


class SeccionPresupuestoInline(admin.TabularInline):
    model = SeccionPresupuesto
    extra = 0


class ItemPresupuestoInline(admin.TabularInline):
    model = ItemPresupuesto
    extra = 0


@admin.register(Presupuesto)
class PresupuestoAdmin(admin.ModelAdmin):
    list_display = ("numero", "cliente", "estado", "fecha", "creado_por")
    list_filter = ("estado",)
    search_fields = ("numero", "cliente__nombre")
    readonly_fields = ("numero",)
    inlines = [SeccionPresupuestoInline, ItemPresupuestoInline]


@admin.register(PlantillaCondiciones)
class PlantillaCondicionesAdmin(admin.ModelAdmin):
    list_display = ("nombre", "predeterminada", "activa")
    list_filter = ("activa",)
