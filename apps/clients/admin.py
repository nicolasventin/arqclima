from django.contrib import admin

from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "cuit_dni", "telefono", "email", "activo")
    search_fields = ("nombre", "cuit_dni", "email")
    list_filter = ("tipo", "activo")
