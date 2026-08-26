from django.contrib import admin

from .models import Trabajo


@admin.register(Trabajo)
class TrabajoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "presupuesto", "tecnico_asignado", "estado", "creado_en")
    list_filter = ("estado",)
