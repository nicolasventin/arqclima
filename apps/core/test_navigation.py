from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


class NavegacionVisualTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="nav_visual",
            password="clave12345",
        )
        self.client.login(
            username="nav_visual",
            password="clave12345",
        )

    def test_shell_autenticado_tiene_sidebar_y_offcanvas(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-sidebar d-none d-lg-flex"')
        self.assertContains(response, 'id="appMobileNav"')
        self.assertContains(response, "img/arqclima-logo-oficial.svg", count=2)
        self.assertNotContains(response, "img/arqclima-mark.svg")

    def test_inicio_queda_marcado_como_activo(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertContains(
            response,
            'app-nav-link active',
        )
        self.assertContains(response, 'aria-current="page"')

    def test_navegacion_no_muestra_secciones_sin_permiso(self):
        response = self.client.get(reverse("dashboard:home"))

        self.assertNotContains(response, ">Comercial<")
        self.assertNotContains(response, ">Inventario<")
        self.assertNotContains(response, ">Reportes<")
        self.assertNotContains(response, "Usuarios y permisos")

    def test_permiso_individual_habilita_solo_su_enlace(self):
        permiso = Permission.objects.get(
            content_type__app_label="clients",
            codename="view_cliente",
        )
        self.user.user_permissions.add(permiso)

        response = self.client.get(reverse("dashboard:home"))

        self.assertContains(response, ">Comercial<")
        self.assertContains(response, ">Clientes<")
        self.assertNotContains(response, ">Presupuestos<")

    def test_assets_visuales_existen_en_staticfiles(self):
        self.assertIsNotNone(finders.find("css/app.css"))
        self.assertIsNotNone(finders.find("img/arqclima-logo-oficial.svg"))


class LoginVisualTests(TestCase):
    def test_login_usa_identidad_arqclima(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "img/arqclima-logo-oficial.svg")
        self.assertContains(response, "Sistema interno de gestión")
        self.assertNotContains(response, 'class="app-sidebar')
