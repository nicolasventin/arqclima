from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User

from .models import Cliente


class ClienteModelTests(TestCase):
    def test_str_es_el_nombre(self):
        cliente = Cliente.objects.create(nombre="Juan Pérez")
        self.assertEqual(str(cliente), "Juan Pérez")

    def test_tipo_por_defecto_es_particular(self):
        cliente = Cliente.objects.create(nombre="Juan Pérez")
        self.assertEqual(cliente.tipo, Cliente.Tipo.PARTICULAR)


class ClienteViewsPermisosTests(TestCase):
    """
    Solo Diego y Rodrigo tienen permisos sobre clientes (migración
    0002_asignar_permisos_roles): el resto del equipo no tiene acceso.
    """

    @classmethod
    def setUpTestData(cls):
        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        for codename in ("view_cliente", "add_cliente", "change_cliente"):
            permiso = Permission.objects.get(codename=codename, content_type__app_label="clients")
            grupo_ventas.permissions.add(permiso)

        grupo_tecnico, _ = Group.objects.get_or_create(name="Técnico de Campo")

        cls.rodrigo = User.objects.create_user(username="rodrigo_cli", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)

        cls.andres = User.objects.create_user(username="andres_cli", password="clave12345")
        cls.andres.groups.add(grupo_tecnico)

        cls.cliente = Cliente.objects.create(nombre="Cliente Existente")

    def test_rodrigo_puede_ver_la_lista(self):
        self.client.login(username="rodrigo_cli", password="clave12345")
        response = self.client.get(reverse("clients:lista"))
        self.assertEqual(response.status_code, 200)

    def test_andres_no_puede_ver_la_lista(self):
        self.client.login(username="andres_cli", password="clave12345")
        response = self.client.get(reverse("clients:lista"))
        self.assertEqual(response.status_code, 403)

    def test_rodrigo_puede_crear_cliente(self):
        self.client.login(username="rodrigo_cli", password="clave12345")
        response = self.client.post(
            reverse("clients:nuevo"),
            {"nombre": "Cliente Nuevo", "tipo": "particular", "activo": "on"},
        )
        self.assertRedirects(response, reverse("clients:lista"))
        self.assertTrue(Cliente.objects.filter(nombre="Cliente Nuevo").exists())

    def test_andres_no_puede_crear_cliente(self):
        self.client.login(username="andres_cli", password="clave12345")
        response = self.client.post(
            reverse("clients:nuevo"),
            {"nombre": "Cliente Intruso", "tipo": "particular", "activo": "on"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Cliente.objects.filter(nombre="Cliente Intruso").exists())
