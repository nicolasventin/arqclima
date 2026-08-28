from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Categoria, Marca, Producto, ProductoProveedor, Proveedor

from .management.commands.seed_margenes import MARGEN_MANO_OBRA, MARGENES_POR_CATEGORIA
from .models import ConfiguracionGeneral, HistorialCosto
from .services import calcular_precio_venta, margen_efectivo, registrar_costo, ultimo_costo_producto


class MargenEfectivoTests(TestCase):
    """Jerarquía de márgenes: producto > marca > categoría > general."""

    def setUp(self):
        self.marca = Marca.objects.create(nombre="Marca X")
        self.categoria = Categoria.objects.create(nombre="Categoría X")
        self.producto = Producto.objects.create(
            marca=self.marca, codigo="P1", nombre="Producto", categoria=self.categoria
        )

    def test_usa_general_si_nada_esta_configurado(self):
        config = ConfiguracionGeneral.obtener()
        margen, origen = margen_efectivo(self.producto)
        self.assertEqual(origen, "general")
        self.assertEqual(margen, config.margen_general)

    def test_usa_categoria_si_esta_configurada(self):
        self.categoria.margen = Decimal("20.00")
        self.categoria.save()

        margen, origen = margen_efectivo(self.producto)
        self.assertEqual(origen, "categoria")
        self.assertEqual(margen, Decimal("20.00"))

    def test_marca_le_gana_a_categoria(self):
        self.categoria.margen = Decimal("20.00")
        self.categoria.save()
        self.marca.margen = Decimal("25.00")
        self.marca.save()

        margen, origen = margen_efectivo(self.producto)
        self.assertEqual(origen, "marca")
        self.assertEqual(margen, Decimal("25.00"))

    def test_producto_le_gana_a_todo(self):
        self.categoria.margen = Decimal("20.00")
        self.categoria.save()
        self.marca.margen = Decimal("25.00")
        self.marca.save()
        self.producto.margen = Decimal("40.00")
        self.producto.save()

        margen, origen = margen_efectivo(self.producto)
        self.assertEqual(origen, "producto")
        self.assertEqual(margen, Decimal("40.00"))


class CalcularPrecioVentaTests(TestCase):
    def test_formula_suma_recargos_y_los_aplica_una_vez(self):
        config = ConfiguracionGeneral.obtener()
        config.flete_pct = Decimal("5.00")
        config.costo_financiero_pct = Decimal("8.00")
        config.margen_general = Decimal("30.00")
        config.save()

        marca = Marca.objects.create(nombre="Marca")
        producto = Producto.objects.create(marca=marca, codigo="C1", nombre="Producto")

        precio, origen = calcular_precio_venta(producto, Decimal("100.00"))

        # 100 * (1 + (5 + 8 + 30) / 100) = 100 * 1.43 = 143.00
        self.assertEqual(precio, Decimal("143.00"))
        self.assertEqual(origen, "general")


class UltimoCostoProductoTests(TestCase):
    """A diferencia de costo_actual(producto_proveedor), agrega entre TODOS los proveedores del producto."""

    def setUp(self):
        self.marca = Marca.objects.create(nombre="Marca Ultimo Costo")
        self.producto = Producto.objects.create(marca=self.marca, codigo="U1", nombre="Producto")
        self.usuario = User.objects.create_user(username="user_ultimo_costo", password="clave12345")

    def test_none_sin_ningun_costo_cargado(self):
        self.assertIsNone(ultimo_costo_producto(self.producto))

    def test_devuelve_el_mas_reciente_entre_varios_proveedores(self):
        proveedor_a = Proveedor.objects.create(nombre_comercial="Proveedor A")
        proveedor_b = Proveedor.objects.create(nombre_comercial="Proveedor B")
        pp_a = ProductoProveedor.objects.create(producto=self.producto, proveedor=proveedor_a)
        pp_b = ProductoProveedor.objects.create(producto=self.producto, proveedor=proveedor_b)

        registrar_costo(pp_a, Decimal("100.00"), self.usuario)
        mas_reciente = registrar_costo(pp_b, Decimal("150.00"), self.usuario)

        historial = ultimo_costo_producto(self.producto)
        self.assertEqual(historial, mas_reciente)
        self.assertEqual(historial.costo, Decimal("150.00"))


class HistorialCostoInmutableTests(TestCase):
    """
    El historial de costos nunca se borra ni se pisa (regla de negocio 3).
    Estos tests van directo por SQL, saltándose el ORM/la app por completo,
    para probar el resguardo real: el trigger de Postgres.
    """

    def setUp(self):
        marca = Marca.objects.create(nombre="Marca")
        proveedor = Proveedor.objects.create(nombre_comercial="Proveedor")
        producto = Producto.objects.create(marca=marca, codigo="H1", nombre="Producto")
        pp = ProductoProveedor.objects.create(producto=producto, proveedor=proveedor)
        self.historial = registrar_costo(pp, Decimal("100.00"), usuario=None)

    def test_no_se_puede_actualizar_via_sql_directo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE pricing_historialcosto SET costo = 999 WHERE id = %s",
                        [self.historial.id],
                    )

    def test_no_se_puede_borrar_via_sql_directo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM pricing_historialcosto WHERE id = %s", [self.historial.id]
                    )

        # sigue existiendo: el DELETE no pasó.
        self.assertTrue(HistorialCosto.objects.filter(pk=self.historial.pk).exists())


class PermisosPreciosTests(TestCase):
    """
    Mismo patrón de permiso a nivel de fila que en Etapa 2, aplicado a
    precios: Gabriel puede registrar costos de su línea de repuestos, pero
    no de catálogo general ni tocar márgenes de nadie.
    """

    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Marca")
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Proveedor")
        cls.producto_repuesto = Producto.objects.create(
            marca=cls.marca, codigo="R1", nombre="Repuesto", es_repuesto=True
        )
        cls.producto_general = Producto.objects.create(
            marca=cls.marca, codigo="G1", nombre="General", es_repuesto=False
        )
        cls.pp_repuesto = ProductoProveedor.objects.create(
            producto=cls.producto_repuesto, proveedor=cls.proveedor
        )
        cls.pp_general = ProductoProveedor.objects.create(
            producto=cls.producto_general, proveedor=cls.proveedor
        )

        grupo_service, _ = Group.objects.get_or_create(name="Service y Repuestos")
        permiso_ver_repuestos = Permission.objects.get(
            codename="view_precio_repuestos", content_type__app_label="pricing"
        )
        permiso_costos_repuestos = Permission.objects.get(
            codename="manage_costos_repuestos", content_type__app_label="pricing"
        )
        grupo_service.permissions.add(permiso_ver_repuestos, permiso_costos_repuestos)

        cls.gabriel = User.objects.create_user(username="gabriel_precios", password="clave12345")
        cls.gabriel.groups.add(grupo_service)

    def test_gabriel_puede_registrar_costo_de_repuesto(self):
        self.client.login(username="gabriel_precios", password="clave12345")
        url = reverse("pricing:registrar_costo", args=[self.producto_repuesto.pk, self.pp_repuesto.pk])

        response = self.client.post(url, {"costo": "150.00"})

        self.assertRedirects(
            response, reverse("catalog:producto_detalle", args=[self.producto_repuesto.pk])
        )
        self.assertTrue(self.pp_repuesto.historial_costos.filter(costo="150.00").exists())

    def test_gabriel_no_puede_registrar_costo_de_producto_general(self):
        self.client.login(username="gabriel_precios", password="clave12345")
        url = reverse("pricing:registrar_costo", args=[self.producto_general.pk, self.pp_general.pk])

        response = self.client.post(url, {"costo": "150.00"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.pp_general.historial_costos.exists())

    def test_gabriel_no_puede_gestionar_margenes(self):
        self.client.login(username="gabriel_precios", password="clave12345")
        url = reverse("pricing:actualizar_margen_producto", args=[self.producto_repuesto.pk])

        response = self.client.post(url, {"margen": "50.00"})

        self.assertEqual(response.status_code, 403)
        self.producto_repuesto.refresh_from_db()
        self.assertIsNone(self.producto_repuesto.margen)


class SeedMargenesCommandTests(TestCase):
    """El comando de carga inicial de márgenes es idempotente: no duplica
    categorías al correrlo dos veces, y actualiza el valor si ya existía."""

    def test_es_idempotente_y_carga_los_valores_de_diego(self):
        call_command("seed_margenes")
        call_command("seed_margenes")

        for nombre, margen in MARGENES_POR_CATEGORIA.items():
            categorias = Categoria.objects.filter(nombre=nombre)
            self.assertEqual(categorias.count(), 1, f"'{nombre}' quedó duplicada")
            self.assertEqual(categorias.first().margen, margen)

        self.assertEqual(ConfiguracionGeneral.obtener().margen_mano_obra, MARGEN_MANO_OBRA)


class ConfiguracionPreciosViewTests(TestCase):
    """
    La pantalla de configuración de márgenes es exclusiva de Diego, y
    permite tocar los 4 niveles (general/mano de obra, categoría, marca,
    producto) desde un solo lugar.
    """

    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Marca Config")
        cls.categoria = Categoria.objects.create(nombre="Categoría Config")
        cls.producto = Producto.objects.create(
            marca=cls.marca, codigo="CFG-1", nombre="Producto Config", categoria=cls.categoria
        )

        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        for codename in ("view_historialcosto", "add_historialcosto", "manage_margenes"):
            permiso = Permission.objects.get(codename=codename, content_type__app_label="pricing")
            grupo_admin.permissions.add(permiso)

        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        permiso_ver = Permission.objects.get(
            codename="view_historialcosto", content_type__app_label="pricing"
        )
        grupo_ventas.permissions.add(permiso_ver)

        cls.diego = User.objects.create_user(username="diego_config", password="clave12345")
        cls.diego.groups.add(grupo_admin)

        cls.rodrigo = User.objects.create_user(username="rodrigo_config", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)

    def test_rodrigo_no_puede_ver_la_pantalla_de_configuracion(self):
        self.client.login(username="rodrigo_config", password="clave12345")
        response = self.client.get(reverse("pricing:configuracion"))
        self.assertEqual(response.status_code, 403)

    def test_diego_puede_actualizar_margen_de_categoria(self):
        self.client.login(username="diego_config", password="clave12345")
        url = reverse("pricing:actualizar_margen_categoria", args=[self.categoria.pk])

        response = self.client.post(url, {"margen": "26.00"})

        self.assertRedirects(response, reverse("pricing:configuracion"))
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.margen, Decimal("26.00"))

    def test_diego_puede_actualizar_margen_de_marca(self):
        self.client.login(username="diego_config", password="clave12345")
        url = reverse("pricing:actualizar_margen_marca", args=[self.marca.pk])

        response = self.client.post(url, {"margen": "33.00"})

        self.assertRedirects(response, reverse("pricing:configuracion"))
        self.marca.refresh_from_db()
        self.assertEqual(self.marca.margen, Decimal("33.00"))

    def test_diego_puede_actualizar_configuracion_general_y_mano_de_obra(self):
        self.client.login(username="diego_config", password="clave12345")
        url = reverse("pricing:actualizar_configuracion_general")

        response = self.client.post(url, {
            "margen_general": "30.00",
            "margen_mano_obra": "40.00",
            "flete_pct": "5.00",
            "costo_financiero_pct": "8.00",
            "margen_minimo_alerta": "15.00",
            "dias_seguimiento_presupuesto_enviado": "3",
            "dias_aviso_presupuesto_por_vencer": "3",
        })

        self.assertRedirects(response, reverse("pricing:configuracion"))
        config = ConfiguracionGeneral.obtener()
        self.assertEqual(config.margen_mano_obra, Decimal("40.00"))

    def test_diego_puede_actualizar_margen_de_producto_desde_la_pantalla_central(self):
        self.client.login(username="diego_config", password="clave12345")
        url = reverse("pricing:actualizar_margen_producto", args=[self.producto.pk])
        destino = reverse("pricing:configuracion") + "?q=CFG"

        response = self.client.post(url, {"margen": "50.00", "next": destino})

        self.assertRedirects(response, destino)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.margen, Decimal("50.00"))

    def test_next_inseguro_se_ignora(self):
        """Un 'next' que no sea una ruta local no debe usarse como destino."""
        self.client.login(username="diego_config", password="clave12345")
        url = reverse("pricing:actualizar_margen_producto", args=[self.producto.pk])

        response = self.client.post(url, {"margen": "50.00", "next": "//evil.example.com/"})

        self.assertRedirects(
            response, reverse("catalog:producto_detalle", args=[self.producto.pk])
        )
