import tempfile
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.pricing.models import HistorialCosto
from apps.pricing.services import registrar_costo

from .models import ImportacionFila, ImportacionListaPrecios
from .parsing import ColumnasNoDetectadas, detectar_columnas, parsear_costo
from .services import confirmar_importacion, procesar_importacion

# Los tests de esta app suben archivos .xlsx reales (ImportacionListaPrecios
# tiene un FileField). Sin este override, Django los escribiría en el
# MEDIA_ROOT real del proyecto y quedarían ensuciando media/ después de
# cada corrida — este directorio temporal se descarta solo al terminar.
_MEDIA_ROOT_TEST = tempfile.mkdtemp(prefix="arqclima-test-media-")


def construir_excel(encabezados, filas):
    """Arma un .xlsx en memoria para usar en los tests, sin depender de archivos en disco."""
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(encabezados)
    for fila in filas:
        hoja.append(fila)
    buffer = BytesIO()
    libro.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        "lista.xlsx", buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class ParsingTests(TestCase):
    def test_detectar_columnas_reconoce_alias_comunes(self):
        mapeo = detectar_columnas(["Marca", "SKU", "Descripción", "Precio Neto"])
        self.assertEqual(mapeo, {"marca": 0, "codigo": 1, "nombre": 2, "costo": 3})

    def test_detectar_columnas_falla_si_falta_una_requerida(self):
        with self.assertRaises(ColumnasNoDetectadas) as cm:
            detectar_columnas(["Marca", "Código", "Descripción"])  # falta costo
        self.assertIn("costo", cm.exception.faltantes)

    def test_parsear_costo_formatos_variados(self):
        self.assertEqual(parsear_costo(1000), Decimal("1000.00"))
        self.assertEqual(parsear_costo(1000.5), Decimal("1000.50"))
        self.assertEqual(parsear_costo("1234,56"), Decimal("1234.56"))
        self.assertEqual(parsear_costo("1.234,56"), Decimal("1234.56"))
        self.assertEqual(parsear_costo("1234.56"), Decimal("1234.56"))
        self.assertIsNone(parsear_costo(""))
        self.assertIsNone(parsear_costo(None))
        self.assertIsNone(parsear_costo("no es un número"))


@override_settings(MEDIA_ROOT=_MEDIA_ROOT_TEST)
class ProcesarImportacionTests(TestCase):
    """
    Cubre las 6 categorías de fila (regla de negocio 4: nuevos, existentes
    -con y sin cambio de costo-, para revisar, errores), usando usuarios
    reales con permisos reales, no atajos de superusuario.
    """

    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Vulcano")
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Distribuidora Test")

        cls.producto_existente = Producto.objects.create(
            marca=cls.marca, codigo="EX-1", nombre="Producto Existente"
        )
        cls.vinculo_existente = ProductoProveedor.objects.create(
            producto=cls.producto_existente, proveedor=cls.proveedor
        )
        registrar_costo(cls.vinculo_existente, Decimal("100.00"), usuario=None)

        cls.producto_sin_vinculo = Producto.objects.create(
            marca=cls.marca, codigo="SV-1", nombre="Producto Sin Vínculo"
        )

        cls.producto_general = Producto.objects.create(
            marca=cls.marca, codigo="GEN-1", nombre="Producto General", es_repuesto=False
        )

        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        for codename, app in (
            ("view_historialcosto", "pricing"), ("add_historialcosto", "pricing"),
            ("add_marca", "catalog"), ("add_producto", "catalog"), ("change_producto", "catalog"),
        ):
            grupo_admin.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )
        cls.diego = User.objects.create_user(username="diego_import", password="clave12345")
        cls.diego.groups.add(grupo_admin)

        grupo_service, _ = Group.objects.get_or_create(name="Service y Repuestos")
        for codename, app in (
            ("view_precio_repuestos", "pricing"), ("manage_costos_repuestos", "pricing"),
            ("manage_repuestos", "catalog"),
        ):
            grupo_service.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )
        cls.gabriel = User.objects.create_user(username="gabriel_import", password="clave12345")
        cls.gabriel.groups.add(grupo_service)

    def _importacion(self, usuario, encabezados, filas):
        archivo = construir_excel(encabezados, filas)
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=usuario
        )
        procesar_importacion(importacion)
        return importacion

    def test_producto_nuevo(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "NUEVO-1", "Producto Totalmente Nuevo", 500]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.NUEVO_PRODUCTO)
        self.assertTrue(fila.incluir)

    def test_nuevo_vinculo_con_producto_ya_existente(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "SV-1", "Producto Sin Vínculo", 300]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.NUEVO_VINCULO)
        self.assertEqual(fila.producto, self.producto_sin_vinculo)

    def test_actualiza_costo(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "EX-1", "Producto Existente", 150]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.ACTUALIZA_COSTO)
        self.assertEqual(fila.costo, Decimal("150.00"))

    def test_sin_cambios_si_el_costo_es_igual(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "EX-1", "Producto Existente", 100]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.SIN_CAMBIOS)
        self.assertFalse(fila.incluir)

    def test_para_revisar_si_el_nombre_no_coincide(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "EX-1", "Nombre Completamente Distinto", 150]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.PARA_REVISAR)
        self.assertIn("Nombre distinto", fila.detalle)

    def test_error_si_faltan_datos_o_costo_invalido(self):
        importacion = self._importacion(
            self.diego,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "", "Sin código", 150], ["Vulcano", "X-1", "Costo inválido", "no-numero"]],
        )
        categorias = list(importacion.filas.values_list("categoria", flat=True))
        self.assertEqual(categorias, [ImportacionFila.Categoria.ERROR, ImportacionFila.Categoria.ERROR])
        self.assertFalse(importacion.filas.filter(incluir=True).exists())

    def test_gabriel_no_puede_tocar_producto_general_via_importacion(self):
        importacion = self._importacion(
            self.gabriel,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "GEN-1", "Producto General", 999]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.PARA_REVISAR)
        self.assertFalse(fila.incluir)

    def test_gabriel_no_puede_crear_marca_nueva(self):
        importacion = self._importacion(
            self.gabriel,
            ["Marca", "Código", "Nombre", "Costo"],
            [["Marca Que No Existe", "N-1", "Producto de marca nueva", 999]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.PARA_REVISAR)
        self.assertIn("marca", fila.detalle.lower())


@override_settings(MEDIA_ROOT=_MEDIA_ROOT_TEST)
class MatcheoPorCodigoProveedorTests(TestCase):
    """
    codigo_proveedor (Etapa 2) como segundo camino de matcheo, prioritario
    sobre marca+código cuando resuelve a un producto — pensado para la
    segunda importación en adelante de un mismo proveedor.
    """

    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Vulcano")
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Distribuidora Test")

        cls.producto_a = Producto.objects.create(
            marca=cls.marca, codigo="A-1", nombre="Termostato X"
        )
        cls.vinculo_a = ProductoProveedor.objects.create(
            producto=cls.producto_a, proveedor=cls.proveedor, codigo_proveedor="SUP-999"
        )
        registrar_costo(cls.vinculo_a, Decimal("100.00"), usuario=None)

        cls.producto_b = Producto.objects.create(
            marca=cls.marca, codigo="B-2", nombre="Producto B"
        )

        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        for codename, app in (
            ("view_historialcosto", "pricing"), ("add_historialcosto", "pricing"),
            ("add_marca", "catalog"), ("add_producto", "catalog"), ("change_producto", "catalog"),
        ):
            grupo_admin.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )
        cls.diego = User.objects.create_user(username="diego_codprov", password="clave12345")
        cls.diego.groups.add(grupo_admin)

    def _importacion(self, encabezados, filas):
        archivo = construir_excel(encabezados, filas)
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=self.diego
        )
        procesar_importacion(importacion)
        return importacion

    def test_matchea_por_codigo_proveedor_aunque_el_codigo_de_texto_no_coincida(self):
        # El proveedor manda de nuevo el mismo ítem, pero esta vez con el
        # código escrito distinto ("A-1-TYPO" no matchea ningún producto
        # por marca+código) — el código de SU catálogo (SUP-999) sigue igual.
        importacion = self._importacion(
            ["Marca", "Código", "Nombre", "Costo", "Codigo Proveedor"],
            [["Vulcano", "A-1-TYPO", "Termostato X", 120, "SUP-999"]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.producto, self.producto_a)
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.ACTUALIZA_COSTO)

    def test_conflicto_entre_codigo_proveedor_y_marca_codigo_va_a_revisar(self):
        # El código de proveedor apunta al producto A, pero la marca+código
        # de esta fila apuntan al producto B: dato inconsistente, no se
        # adivina cuál es el correcto.
        importacion = self._importacion(
            ["Marca", "Código", "Nombre", "Costo", "Codigo Proveedor"],
            [["Vulcano", "B-2", "Producto B", 200, "SUP-999"]],
        )
        fila = importacion.filas.get()
        self.assertEqual(fila.categoria, ImportacionFila.Categoria.PARA_REVISAR)
        self.assertIsNone(fila.producto)
        self.assertFalse(fila.incluir)
        self.assertIn("SUP-999", fila.detalle)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT_TEST)
class ConfirmarImportacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Vulcano")
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Distribuidora Test")

        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        for codename, app in (
            ("view_historialcosto", "pricing"), ("add_historialcosto", "pricing"),
            ("add_marca", "catalog"), ("add_producto", "catalog"), ("change_producto", "catalog"),
        ):
            grupo_admin.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )
        cls.diego = User.objects.create_user(username="diego_confirmar", password="clave12345")
        cls.diego.groups.add(grupo_admin)

        grupo_service, _ = Group.objects.get_or_create(name="Service y Repuestos")
        for codename, app in (
            ("view_precio_repuestos", "pricing"), ("manage_costos_repuestos", "pricing"),
            ("manage_repuestos", "catalog"),
        ):
            grupo_service.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label=app)
            )
        cls.gabriel = User.objects.create_user(username="gabriel_confirmar", password="clave12345")
        cls.gabriel.groups.add(grupo_service)

    def test_confirmar_crea_producto_y_costo_con_origen_trazable(self):
        archivo = construir_excel(
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "NUEVO-2", "Producto Nuevo", 750]],
        )
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=self.diego
        )
        procesar_importacion(importacion)

        contadores = confirmar_importacion(importacion, self.diego)

        self.assertEqual(contadores["creados"], 1)
        producto = Producto.objects.get(marca=self.marca, codigo="NUEVO-2")
        self.assertFalse(producto.es_repuesto)
        historial = HistorialCosto.objects.get(producto_proveedor__producto=producto)
        self.assertEqual(historial.costo, Decimal("750.00"))
        self.assertEqual(historial.origen, f"importación #{importacion.pk}")

        importacion.refresh_from_db()
        self.assertEqual(importacion.estado, ImportacionListaPrecios.Estado.CONFIRMADA)
        self.assertEqual(importacion.confirmada_por, self.diego)

    def test_fila_destildada_no_se_aplica(self):
        archivo = construir_excel(
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "NUEVO-3", "Producto que no quiero importar", 750]],
        )
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=self.diego
        )
        procesar_importacion(importacion)
        importacion.filas.update(incluir=False)

        contadores = confirmar_importacion(importacion, self.diego)

        self.assertEqual(contadores["creados"], 0)
        self.assertFalse(Producto.objects.filter(codigo="NUEVO-3").exists())

    def test_gabriel_confirma_producto_nuevo_forzado_a_repuesto(self):
        archivo = construir_excel(
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "REP-NUEVO", "Repuesto Nuevo", 300]],
        )
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=self.gabriel
        )
        procesar_importacion(importacion)

        confirmar_importacion(importacion, self.gabriel)

        producto = Producto.objects.get(marca=self.marca, codigo="REP-NUEVO")
        self.assertTrue(producto.es_repuesto)

    def test_descartar_no_toca_el_catalogo(self):
        archivo = construir_excel(
            ["Marca", "Código", "Nombre", "Costo"],
            [["Vulcano", "DESCARTADO-1", "No debería crearse", 750]],
        )
        importacion = ImportacionListaPrecios.objects.create(
            proveedor=self.proveedor, archivo=archivo, cargado_por=self.diego
        )
        procesar_importacion(importacion)
        importacion.estado = ImportacionListaPrecios.Estado.DESCARTADA
        importacion.save()

        self.assertFalse(Producto.objects.filter(codigo="DESCARTADO-1").exists())


@override_settings(MEDIA_ROOT=_MEDIA_ROOT_TEST)
class VistasImportacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Distribuidora Test")

        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        grupo_ventas.permissions.add(
            Permission.objects.get(codename="view_historialcosto", content_type__app_label="pricing")
        )
        cls.rodrigo = User.objects.create_user(username="rodrigo_import", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)

    def test_rodrigo_no_puede_acceder_a_nueva_importacion(self):
        self.client.login(username="rodrigo_import", password="clave12345")
        response = self.client.get(reverse("imports:nueva"))
        self.assertEqual(response.status_code, 403)

    def test_archivo_sin_columnas_reconocibles_se_rechaza_con_mensaje(self):
        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        grupo_admin.permissions.add(
            Permission.objects.get(codename="add_historialcosto", content_type__app_label="pricing")
        )
        diego = User.objects.create_user(username="diego_vista", password="clave12345")
        diego.groups.add(grupo_admin)

        archivo = construir_excel(["Columna A", "Columna B"], [["x", "y"]])
        self.client.login(username="diego_vista", password="clave12345")

        response = self.client.post(
            reverse("imports:nueva"),
            {"proveedor": self.proveedor.pk, "archivo": archivo},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se pudieron reconocer las columnas")
        self.assertEqual(ImportacionListaPrecios.objects.count(), 0)
