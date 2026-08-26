from django.contrib import admin

from .models import Tarea


@admin.register(Tarea)
class TareaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "asignado_a", "asignado_por", "estado", "prioridad", "fecha_limite")
    list_filter = ("estado", "prioridad")
    search_fields = ("titulo",)
