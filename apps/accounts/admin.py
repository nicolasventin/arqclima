from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Datos adicionales", {"fields": ("telefono",)}),
    )
    list_display = ("username", "first_name", "last_name", "email", "is_staff", "rol")

    def rol(self, obj):
        return obj.rol

    rol.short_description = "Rol"
