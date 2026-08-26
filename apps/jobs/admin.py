from django.contrib import admin

from .models import EtapaTrabajo, MaterialTrabajo, Trabajo


class EtapaTrabajoInline(admin.TabularInline):
    model = EtapaTrabajo
    extra = 0


class MaterialTrabajoInline(admin.TabularInline):
    model = MaterialTrabajo
    extra = 0


@admin.register(Trabajo)
class TrabajoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "presupuesto", "tecnico_asignado", "estado", "creado_en")
    list_filter = ("estado",)
    inlines = [EtapaTrabajoInline, MaterialTrabajoInline]
