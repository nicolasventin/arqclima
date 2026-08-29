from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, Proveedor
from apps.clients.models import Cliente
from apps.jobs.services import crear_trabajo
from apps.purchasing.models import OrdenDeCompra
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto
from apps.stock.models import Deposito


def _admin(username="diego_11d"):
    grupo, _ = Group.objects.get_or_create(name="Administrador")
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class DetallesYFormulariosEtapa11DTests(TestCase):
    """Regresiones de render del sistema visual 11D sobre los flujos principales."""

    def setUp(self):
        self.diego = _admin()
        self.client.login(username="diego_11d", password="clave12345")

    def test_formularios_principales_usan_form_card(self):
        rutas = [
            reverse("clients:nuevo"),
            reverse("catalog:producto_nuevo"),
            reverse("catalog:proveedor_nuevo"),
            reverse("quotes:nuevo"),
            reverse("purchasing:nueva"),
            reverse("tasks:nueva"),
            reverse("stock:entrada", args=[Deposito.GENERAL]),
            reverse("stock:ajuste"),
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                response = self.client.get(ruta)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "form-card")
                self.assertContains(response, "detail-header")

    def test_detalle_producto_usa_grilla_y_secciones(self):
        marca = Marca.objects.create(nombre="Marca 11D")
        producto = Producto.objects.create(
            marca=marca,
            codigo="DET-11D",
            nombre="Producto detalle",
        )

        response = self.client.get(
            reverse("catalog:producto_detalle", args=[producto.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail-grid")
        self.assertContains(response, "Información")
        self.assertContains(response, "Proveedores")
        self.assertContains(response, "Stock")

    def _presupuesto_aceptado(self, nombre="Cliente 11D"):
        cliente = Cliente.objects.create(nombre=nombre)
        presupuesto = Presupuesto.objects.create(
            cliente=cliente,
            direccion="San Martín 123",
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            descripcion_manual="Instalación",
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
            costo_unitario=Decimal("50"),
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        return presupuesto

    def test_detalle_presupuesto_muestra_resumen_y_workflow(self):
        presupuesto = self._presupuesto_aceptado()

        response = self.client.get(
            reverse("quotes:detalle", args=[presupuesto.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail-grid")
        self.assertContains(response, "summary-table")
        self.assertContains(response, "Exportar PDF")
        self.assertContains(response, "Ítems")

    def test_detalle_trabajo_mantiene_materiales_y_estado(self):
        presupuesto = self._presupuesto_aceptado("Cliente Trabajo 11D")
        trabajo = crear_trabajo(presupuesto, self.diego)

        response = self.client.get(
            reverse("jobs:detalle", args=[trabajo.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail-grid")
        self.assertContains(response, "Listado de materiales")
        self.assertContains(response, f"Presupuesto #{presupuesto.numero}")
        self.assertContains(response, "Generar listado desde el presupuesto")

    def test_detalle_orden_muestra_timeline_y_lineas(self):
        proveedor = Proveedor.objects.create(nombre_comercial="Proveedor 11D")
        orden = OrdenDeCompra.objects.create(
            proveedor=proveedor,
            deposito_destino=Deposito.GENERAL,
            creado_por=self.diego,
        )

        response = self.client.get(
            reverse("purchasing:detalle", args=[orden.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historial del ciclo")
        self.assertContains(response, "timeline-grid")
        self.assertContains(response, "Líneas")
        self.assertContains(response, "Agregar línea")

    def test_configuracion_precios_usa_nuevo_sistema(self):
        response = self.client.get(reverse("pricing:configuracion"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "detail-grid")
        self.assertContains(response, "Configuración general")
        self.assertContains(response, "Márgenes por categoría")

    def test_importacion_nueva_usa_formulario_asistido(self):
        response = self.client.get(reverse("imports:nueva"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "form-card")
        self.assertContains(response, "Primero se genera una vista previa")
