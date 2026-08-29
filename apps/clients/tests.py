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
        grupo_ventas.permissions.add(
            Permission.objects.get(
                codename="add_presupuesto",
                content_type__app_label="quotes",
            )
        )

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

    def test_busqueda_encuentra_por_nombre_cuit_telefono_y_email(self):
        Cliente.objects.create(
            nombre="Constructora Andes",
            cuit_dni="30-12345678-9",
            telefono="2615557788",
            email="compras@andes.test",
        )
        self.client.login(username="rodrigo_cli", password="clave12345")

        for termino in ("Andes", "12345678", "5557788", "compras@andes"):
            with self.subTest(termino=termino):
                response = self.client.get(reverse("clients:buscar"), {"q": termino})
                self.assertEqual(response.status_code, 200)
                nombres = [fila["nombre"] for fila in response.json()["resultados"]]
                self.assertIn("Constructora Andes", nombres)

    def test_busqueda_no_devuelve_clientes_inactivos(self):
        Cliente.objects.create(nombre="Cliente Archivado", activo=False)
        self.client.login(username="rodrigo_cli", password="clave12345")

        response = self.client.get(reverse("clients:buscar"), {"q": "Archivado"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resultados"], [])

    def test_busqueda_corta_no_carga_clientes(self):
        self.client.login(username="rodrigo_cli", password="clave12345")

        response = self.client.get(reverse("clients:buscar"), {"q": "a"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resultados"], [])

    def test_andres_no_puede_usar_busqueda_de_presupuesto(self):
        self.client.login(username="andres_cli", password="clave12345")

        response = self.client.get(reverse("clients:buscar"), {"q": "Cliente"})

        self.assertEqual(response.status_code, 403)

    def test_alta_rapida_crea_cliente_activo_y_devuelve_json(self):
        self.client.login(username="rodrigo_cli", password="clave12345")

        response = self.client.post(
            reverse("clients:nuevo_rapido"),
            {
                "nombre": "Cliente Express",
                "tipo": "empresa",
                "cuit_dni": "30-99999999-1",
                "telefono": "2614445566",
                "email": "express@example.com",
            },
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data["ok"])
        cliente = Cliente.objects.get(pk=data["cliente"]["id"])
        self.assertEqual(cliente.nombre, "Cliente Express")
        self.assertTrue(cliente.activo)

    def test_alta_rapida_valida_datos(self):
        self.client.login(username="rodrigo_cli", password="clave12345")

        response = self.client.post(
            reverse("clients:nuevo_rapido"),
            {
                "nombre": "",
                "tipo": "particular",
                "email": "no-es-email",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("nombre", response.json()["errores"])
        self.assertIn("email", response.json()["errores"])

    def test_andres_no_puede_hacer_alta_rapida(self):
        self.client.login(username="andres_cli", password="clave12345")

        response = self.client.post(
            reverse("clients:nuevo_rapido"),
            {"nombre": "Cliente Intruso", "tipo": "particular"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Cliente.objects.filter(nombre="Cliente Intruso").exists())
