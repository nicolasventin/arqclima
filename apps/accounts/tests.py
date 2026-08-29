from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import (
    ADMINISTRADOR,
    ROLES,
    TECNICO_DE_CAMPO,
)


class PermisosUsuariosViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.grupos = {
            nombre: Group.objects.get_or_create(name=nombre)[0]
            for nombre in ROLES
        }
        cls.grupo_intruso = Group.objects.create(name="Rol inventado")

        cls.permiso_gestion = Permission.objects.get(
            codename="manage_permissions",
            content_type__app_label="accounts",
        )
        cls.grupos[ADMINISTRADOR].permissions.add(cls.permiso_gestion)

        cls.admin = User.objects.create_user(
            username="admin_permisos",
            password="clave12345",
        )
        cls.admin.groups.set([cls.grupos[ADMINISTRADOR]])

        cls.tecnico = User.objects.create_user(
            username="tecnico_permisos",
            password="clave12345",
        )
        cls.tecnico.groups.set([cls.grupos[TECNICO_DE_CAMPO]])

    def setUp(self):
        self.client.login(username="admin_permisos", password="clave12345")

    def test_muestra_permisos_de_todos_los_modulos_de_negocio(self):
        response = self.client.get(reverse("accounts:permisos"))

        self.assertEqual(response.status_code, 200)
        fila_admin = next(
            fila
            for fila in response.context["usuarios_data"]
            if fila["usuario"].pk == self.admin.pk
        )
        apps_mostradas = {modulo["label"] for modulo in fila_admin["permisos_por_app"]}

        self.assertTrue(
            {
                "accounts",
                "audit",
                "catalog",
                "clients",
                "jobs",
                "pricing",
                "purchasing",
                "quotes",
                "reports",
                "stock",
                "tasks",
            }.issubset(apps_mostradas)
        )

    def test_solo_ofrece_los_cinco_roles_validos(self):
        response = self.client.get(reverse("accounts:permisos"))

        nombres = {grupo.name for grupo in response.context["grupos"]}
        self.assertEqual(nombres, set(ROLES))
        self.assertNotIn(self.grupo_intruso.name, nombres)

    def test_rechaza_un_group_que_no_sea_un_rol_de_arqclima(self):
        response = self.client.post(
            reverse("accounts:permisos"),
            {
                "user_id": self.tecnico.pk,
                "group_id": self.grupo_intruso.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.tecnico.refresh_from_db()
        self.assertEqual(self.tecnico.rol, TECNICO_DE_CAMPO)

    def test_post_manipulado_no_puede_agregar_permiso_de_django_interno(self):
        permiso_interno = Permission.objects.get(
            codename="add_group",
            content_type__app_label="auth",
        )

        response = self.client.post(
            reverse("accounts:permisos"),
            {
                "user_id": self.tecnico.pk,
                "group_id": self.grupos[TECNICO_DE_CAMPO].pk,
                "permissions": [permiso_interno.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            self.tecnico.user_permissions.filter(pk=permiso_interno.pk).exists()
        )

    def test_puede_otorgar_override_de_un_modulo_de_negocio(self):
        permiso_stock = Permission.objects.get(
            codename="view_movimientostock",
            content_type__app_label="stock",
        )

        response = self.client.post(
            reverse("accounts:permisos"),
            {
                "user_id": self.tecnico.pk,
                "group_id": self.grupos[TECNICO_DE_CAMPO].pk,
                "permissions": [permiso_stock.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.tecnico.user_permissions.filter(pk=permiso_stock.pk).exists()
        )

    def test_no_puede_quitarse_su_propio_acceso_a_gestionar_permisos(self):
        response = self.client.post(
            reverse("accounts:permisos"),
            {
                "user_id": self.admin.pk,
                "group_id": self.grupos[TECNICO_DE_CAMPO].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.rol, ADMINISTRADOR)
        self.assertTrue(self.admin.has_perm("accounts.manage_permissions"))
