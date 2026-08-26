from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("creado_en", "usuario", "accion", "objeto_repr")
    list_filter = ("accion", "creado_en")
    search_fields = ("usuario__username", "accion", "detalle", "objeto_repr")
    date_hierarchy = "creado_en"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
