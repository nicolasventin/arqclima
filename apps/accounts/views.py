from django.contrib import messages
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View

from apps.audit.services import log_action

from .models import User


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

# Apps propias cuyos permisos tiene sentido mostrar/gestionar en esta
# pantalla. A medida que se agreguen módulos futuros (catálogo,
# presupuestos, stock, compras, etc.) sus permisos van a aparecer acá
# automáticamente, sin tener que tocar esta vista.
APPS_GESTIONABLES = ["accounts", "audit"]


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

        nuevo_grupo_id = request.POST.get("group_id")
        if nuevo_grupo_id:
            grupo_anterior = usuario.rol
            usuario.groups.set([nuevo_grupo_id])
            grupo_nuevo = Group.objects.get(pk=nuevo_grupo_id)
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

        permisos_gestionables = Permission.objects.filter(
            content_type__app_label__in=APPS_GESTIONABLES
        )
        permisos_marcados = set(request.POST.getlist("permissions"))
        permisos_antes = {
            str(p.pk) for p in usuario.user_permissions.filter(pk__in=permisos_gestionables)
        }

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
        grupos = list(Group.objects.all().order_by("name"))
        permisos = list(
            Permission.objects.filter(content_type__app_label__in=APPS_GESTIONABLES)
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

            filas_permisos = [
                {
                    "id": permiso.id,
                    "nombre": permiso.name,
                    "por_rol": permiso.id in permisos_del_rol,
                    "activo": permiso.id in permisos_del_rol or permiso.id in permisos_override,
                }
                for permiso in permisos
            ]

            usuarios_data.append(
                {
                    "usuario": usuario,
                    "grupo_actual_id": grupo_actual.id if grupo_actual else None,
                    "permisos": filas_permisos,
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
