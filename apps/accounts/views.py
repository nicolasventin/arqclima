from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View

from apps.audit.services import log_action

from .models import User
from .roles import ADMINISTRADOR, ROLES


class PasswordChangeView(DjangoPasswordChangeView):
    """Pantalla de 'cambiar mi contraseña' para cualquier usuario logueado."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:cambiar_password_hecho")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            user=self.request.user,
            action="change_password",
            detail="El usuario cambió su propia contraseña",
        )
        return response


NOMBRES_APPS = {
    "accounts": "Usuarios y permisos",
    "audit": "Auditoría",
    "catalog": "Catálogo",
    "clients": "Clientes",
    "imports": "Importaciones",
    "jobs": "Trabajos",
    "pricing": "Precios",
    "purchasing": "Compras",
    "quotes": "Presupuestos",
    "reports": "Reportes",
    "stock": "Stock",
    "tasks": "Tareas",
}


def apps_gestionables():
    """
    Devuelve las apps propias del proyecto que tienen permisos administrables.

    Se deriva del registro de apps de Django en vez de mantener una lista
    manual: cualquier módulo futuro bajo apps.* aparece automáticamente, sin
    exponer permisos internos de django.contrib ni de paquetes de terceros.
    """
    return {
        config.label
        for config in django_apps.get_app_configs()
        if config.name.startswith("apps.")
    }


class PermisosUsuariosView(View):
    permission_required = "accounts.manage_permissions"
    template_name = "accounts/permisos.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not request.user.has_perm(self.permission_required):
            return render(request, "403.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        usuario = get_object_or_404(User, pk=request.POST.get("user_id"))

        # Solo existen cinco roles válidos de negocio. No se acepta cualquier
        # Group que alguien pueda crear desde el admin o mediante un script.
        nuevo_grupo_id = request.POST.get("group_id")
        if not nuevo_grupo_id:
            messages.error(request, "El usuario debe tener uno de los roles válidos de ARQCLIMA.")
            return self._render(request)

        try:
            grupo_nuevo = Group.objects.get(pk=nuevo_grupo_id, name__in=ROLES)
        except (Group.DoesNotExist, TypeError, ValueError):
            messages.error(request, "El rol seleccionado no es válido para ARQCLIMA.")
            return self._render(request)

        permisos_gestionables = Permission.objects.filter(
            content_type__app_label__in=apps_gestionables()
        )
        ids_gestionables = {
            str(pk) for pk in permisos_gestionables.values_list("pk", flat=True)
        }

        # Nunca confiar en IDs enviados por el navegador: solo se admiten
        # permisos pertenecientes a apps propias del proyecto.
        permisos_marcados = set(request.POST.getlist("permissions")) & ids_gestionables
        permisos_antes = {
            str(p.pk)
            for p in usuario.user_permissions.filter(pk__in=permisos_gestionables)
        }

        # Protección anti-lockout: quien está administrando permisos no puede
        # quitarse a sí mismo el acceso a esta misma pantalla. Esto cubre al
        # Administrador normal y también a un eventual usuario delegado.
        if usuario.pk == request.user.pk:
            permiso_gestion = Permission.objects.get(
                codename="manage_permissions",
                content_type__app_label="accounts",
            )
            conserva_gestion = (
                grupo_nuevo.permissions.filter(pk=permiso_gestion.pk).exists()
                or str(permiso_gestion.pk) in permisos_marcados
            )
            if not conserva_gestion:
                messages.error(
                    request,
                    "No podés quitarte tu propio permiso para administrar usuarios y permisos.",
                )
                return self._render(request)

        grupo_anterior = usuario.rol
        usuario.groups.set([grupo_nuevo])
        if grupo_anterior != grupo_nuevo.name:
            log_action(
                user=request.user,
                action="change_role",
                obj=usuario,
                detail=(
                    f"Rol de {usuario.username} cambiado de "
                    f"'{grupo_anterior}' a '{grupo_nuevo.name}'"
                ),
            )

        nuevos = permisos_marcados - permisos_antes
        quitados = permisos_antes - permisos_marcados

        if nuevos:
            usuario.user_permissions.add(*nuevos)
        if quitados:
            usuario.user_permissions.remove(*quitados)

        if nuevos or quitados:
            log_action(
                user=request.user,
                action="change_permissions",
                obj=usuario,
                detail=(
                    f"Permisos individuales otorgados: {len(nuevos)}, "
                    f"revocados: {len(quitados)} para {usuario.username}"
                ),
            )

        messages.success(
            request,
            f"Permisos de {usuario.get_full_name() or usuario.username} actualizados.",
        )
        return self._render(request)

    def _render(self, request):
        grupos = list(Group.objects.filter(name__in=ROLES).order_by("name"))
        permisos = list(
            Permission.objects.filter(content_type__app_label__in=apps_gestionables())
            .select_related("content_type")
            .order_by("content_type__app_label", "codename")
        )

        usuarios_data = []
        for usuario in User.objects.all().order_by("first_name", "username").prefetch_related(
            "groups", "user_permissions"
        ):
            grupo_actual = usuario.groups.first()
            permisos_del_rol = set(
                Permission.objects.filter(group__user=usuario).values_list("id", flat=True)
            )
            permisos_override = set(usuario.user_permissions.values_list("id", flat=True))

            permisos_por_app = []
            app_actual = None
            modulo_actual = None
            for permiso in permisos:
                app_label = permiso.content_type.app_label
                if app_label != app_actual:
                    modulo_actual = {
                        "label": app_label,
                        "nombre": NOMBRES_APPS.get(app_label, app_label.replace("_", " ").title()),
                        "permisos": [],
                    }
                    permisos_por_app.append(modulo_actual)
                    app_actual = app_label

                modulo_actual["permisos"].append(
                    {
                        "id": permiso.id,
                        "nombre": permiso.name,
                        "por_rol": permiso.id in permisos_del_rol,
                        "activo": permiso.id in permisos_del_rol or permiso.id in permisos_override,
                    }
                )

            usuarios_data.append(
                {
                    "usuario": usuario,
                    "grupo_actual_id": grupo_actual.id if grupo_actual else None,
                    "permisos_por_app": permisos_por_app,
                }
            )

        return render(
            request,
            self.template_name,
            {
                "usuarios_data": usuarios_data,
                "grupos": grupos,
            },
        )
