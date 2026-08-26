from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User

from .models import Categoria, Marca, Producto


class PermisosRepuestosTests(TestCase):
    """
    Cubre la regla de negocio de permiso a nivel de fila: Gabriel (rol
    Service y Repuestos) solo puede crear/editar productos con
    es_repuesto=True, aunque intente vía POST directo saltándose la UI.

    Si en el futuro alguien reemplaza puede_editar_producto() por un
    chequeo de permiso genérico (perdiendo la restricción por fila), estos
    tests tienen que empezar a fallar.
    """

    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Marca Test")
        cls.categoria = Categoria.objects.create(nombre="Categoría Test")

        cls.producto_repuesto = Producto.objects.create(
            marca=cls.marca, codigo="REP-001", nombre="Repuesto de prueba",
            categoria=cls.categoria, es_repuesto=True,
        )
        cls.producto_general = Producto.objects.create(
            marca=cls.marca, codigo="GEN-001", nombre="Producto general de prueba",
            categoria=cls.categoria, es_repuesto=False,
        )

        grupo_service, _ = Group.objects.get_or_create(name="Service y Repuestos")
        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")

        permiso_repuestos = Permission.objects.get(
            codename="manage_repuestos", content_type__app_label="catalog"
        )
        permiso_ver = Permission.objects.get(
            codename="view_producto", content_type__app_label="catalog"
        )
        grupo_service.permissions.add(permiso_repuestos, permiso_ver)

        permiso_cambiar = Permission.objects.get(
            codename="change_producto", content_type__app_label="catalog"
        )
        permiso_crear = Permission.objects.get(
            codename="add_producto", content_type__app_label="catalog"
        )
        grupo_admin.permissions.add(permiso_cambiar, permiso_crear, permiso_ver)

        cls.gabriel = User.objects.create_user(username="gabriel_test", password="clave12345")
        cls.gabriel.groups.add(grupo_service)

        # A propósito NO es superusuario: así el test verifica de verdad el
        # permiso de grupo, no un atajo de is_superuser.
        cls.diego = User.objects.create_user(username="diego_test", password="clave12345")
        cls.diego.groups.add(grupo_admin)

    def _datos_producto(self, producto, **overrides):
        datos = {
            "marca": self.marca.pk,
            "codigo": producto.codigo,
            "nombre": producto.nombre,
            "categoria": self.categoria.pk,
            "unidad_medida": "unidad",
            "es_repuesto": "on" if producto.es_repuesto else "",
            "activo": "on",
        }
        datos.update(overrides)
        return datos

    def test_gabriel_no_puede_editar_producto_general_via_post_directo(self):
        self.client.login(username="gabriel_test", password="clave12345")
        url = reverse("catalog:producto_editar", args=[self.producto_general.pk])

        response = self.client.post(
            url,
            self._datos_producto(
                self.producto_general,
                nombre="Intento de edición no autorizada",
                es_repuesto="on",  # incluso si intenta forzarlo
            ),
        )

        self.assertEqual(response.status_code, 403)
        self.producto_general.refresh_from_db()
        self.assertEqual(self.producto_general.nombre, "Producto general de prueba")

    def test_gabriel_puede_editar_producto_de_su_linea(self):
        self.client.login(username="gabriel_test", password="clave12345")
        url = reverse("catalog:producto_editar", args=[self.producto_repuesto.pk])

        response = self.client.post(
            url,
            self._datos_producto(self.producto_repuesto, nombre="Repuesto editado por Gabriel"),
        )

        self.assertRedirects(
            response, reverse("catalog:producto_detalle", args=[self.producto_repuesto.pk])
        )
        self.producto_repuesto.refresh_from_db()
        self.assertEqual(self.producto_repuesto.nombre, "Repuesto editado por Gabriel")

    def test_gabriel_no_puede_crear_producto_fuera_de_su_linea_via_post_directo(self):
        self.client.login(username="gabriel_test", password="clave12345")
        url = reverse("catalog:producto_nuevo")

        response = self.client.post(url, {
            "marca": self.marca.pk,
            "codigo": "NUEVO-001",
            "nombre": "Producto creado por Gabriel",
            "categoria": self.categoria.pk,
            "unidad_medida": "unidad",
            "es_repuesto": "",  # intenta crearlo FUERA de la línea de repuestos
            "activo": "on",
        })

        self.assertEqual(response.status_code, 302)
        creado = Producto.objects.get(codigo="NUEVO-001")
        self.assertTrue(
            creado.es_repuesto,
            "Un producto creado por alguien que solo tiene manage_repuestos "
            "debe quedar forzado a es_repuesto=True.",
        )

    def test_diego_puede_editar_cualquier_producto(self):
        self.client.login(username="diego_test", password="clave12345")
        url = reverse("catalog:producto_editar", args=[self.producto_general.pk])

        response = self.client.post(
            url, self._datos_producto(self.producto_general, nombre="Editado por Diego")
        )

        self.assertRedirects(
            response, reverse("catalog:producto_detalle", args=[self.producto_general.pk])
        )
        self.producto_general.refresh_from_db()
        self.assertEqual(self.producto_general.nombre, "Editado por Diego")

    def test_diego_puede_desmarcar_es_repuesto_y_gabriel_pierde_acceso(self):
        """
        Caso borde confirmado explícitamente: si Diego le saca la marca de
        'repuesto' a un producto, Gabriel deja de poder editarlo a partir
        de ese momento. El chequeo es sobre el estado ACTUAL del producto,
        no sobre quién lo creó ni sobre su historial. Es el comportamiento
        esperado, no un efecto secundario — por eso está fijado acá como
        test explícito y no solo mencionado en un comentario.
        """
        self.client.login(username="diego_test", password="clave12345")
        url_editar = reverse("catalog:producto_editar", args=[self.producto_repuesto.pk])
        self.client.post(
            url_editar,
            self._datos_producto(self.producto_repuesto, es_repuesto=""),  # Diego lo desmarca
        )
        self.producto_repuesto.refresh_from_db()
        self.assertFalse(self.producto_repuesto.es_repuesto)
        self.client.logout()

        self.client.login(username="gabriel_test", password="clave12345")
        response = self.client.post(
            url_editar,
            self._datos_producto(
                self.producto_repuesto,
                nombre="Gabriel ya no debería poder tocar esto",
                es_repuesto="on",
            ),
        )
        self.assertEqual(response.status_code, 403)
