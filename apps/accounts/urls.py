from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("permisos/", views.PermisosUsuariosView.as_view(), name="permisos"),
    path(
        "cambiar-password/",
        views.PasswordChangeView.as_view(),
        name="cambiar_password",
    ),
    path(
        "cambiar-password/hecho/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html"
        ),
        name="cambiar_password_hecho",
    ),
]
