from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto
from apps.clients.models import Cliente
from apps.jobs.models import MaterialTrabajo, Trabajo
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.stock.models import Deposito, MovimientoStock, TipoMovimiento
from apps.stock.permissions import puede_forzar_stock_negativo
from apps.stock.services import (
    StockInsuficienteError,
    registrar_movimiento,
    stock_actual,
)


def _usuario(username, rol):
    grupo = Group.objects.get(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class ControlStockNegativoServicioTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_stock_negativo", "Administrador")
        self.contri = _usuario("contri_stock_negativo", "Depósito")
        marca = Marca.objects.create(nombre="Marca Stock Negativo")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="NEG-1",
            nombre="Producto Stock Negativo",
        )

    def cargar(self, cantidad):
        return registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal(cantidad),
            usuario=self.diego,
        )

    def test_salida_normal_no_puede_dejar_stock_negativo(self):
        self.cargar("2")

        with self.assertRaises(StockInsuficienteError) as ctx:
            registrar_movimiento(
                producto=self.producto,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-5"),
                usuario=self.contri,
            )

        self.assertEqual(ctx.exception.stock_disponible, Decimal("2"))
        self.assertEqual(ctx.exception.cantidad_retirada, Decimal("5"))
        self.assertEqual(ctx.exception.stock_resultante, Decimal("-3"))
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("2"))

    def test_usuario_sin_permiso_no_puede_forzar(self):
        self.cargar("2")
        self.assertFalse(puede_forzar_stock_negativo(self.contri))

        with self.assertRaises(PermissionError):
            registrar_movimiento(
                producto=self.producto,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-5"),
                usuario=self.contri,
                forzar_stock_negativo=True,
                motivo_forzado="Entrega urgente",
            )

        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("2"))

    def test_diego_puede_forzar_con_motivo_y_queda_auditado(self):
        self.cargar("2")
        self.assertTrue(puede_forzar_stock_negativo(self.diego))

        movimiento = registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"),
            usuario=self.diego,
            forzar_stock_negativo=True,
            motivo_forzado="Material ya entregado en obra",
        )

        self.assertTrue(movimiento.forzado_stock_negativo)
        self.assertEqual(movimiento.motivo_forzado, "Material ya entregado en obra")
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("-3"))

        log = AuditLog.objects.latest("id")
        self.assertEqual(log.accion, "salida_stock_forzada")
        self.assertIn("Material ya entregado en obra", log.detalle)
        self.assertIn("stock_antes=2", log.detalle)
        self.assertIn("cantidad_retirada=5", log.detalle)
        self.assertIn("stock_despues=-3", log.detalle)

    def test_forzado_requiere_motivo(self):
        self.cargar("2")

        with self.assertRaisesMessage(ValueError, "Debe indicar el motivo"):
            registrar_movimiento(
                producto=self.producto,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-5"),
                usuario=self.diego,
                forzar_stock_negativo=True,
                motivo_forzado="   ",
            )

        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("2"))

    def test_entrada_puede_recuperar_parcialmente_un_saldo_negativo(self):
        self.cargar("2")
        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"),
            usuario=self.diego,
            forzar_stock_negativo=True,
            motivo_forzado="Salida excepcional",
        )

        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("2"),
            usuario=self.contri,
        )

        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("-1"))


class ControlStockNegativoBaseTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_trigger_stock_negativo", "Administrador")
        marca = Marca.objects.create(nombre="Marca Trigger Stock Negativo")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="NEG-DB",
            nombre="Producto Trigger",
        )

    def test_insert_directo_no_puede_dejar_stock_negativo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto,
                    deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.SALIDA,
                    cantidad=Decimal("-1"),
                    registrado_por=self.diego,
                )

        self.assertEqual(MovimientoStock.objects.count(), 0)

    def test_insert_forzado_directo_requiere_motivo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto,
                    deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.SALIDA,
                    cantidad=Decimal("-1"),
                    registrado_por=self.diego,
                    forzado_stock_negativo=True,
                    motivo_forzado="",
                )

    def test_insert_forzado_explicito_con_motivo_es_valido(self):
        movimiento = MovimientoStock.objects.create(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-1"),
            registrado_por=self.diego,
            forzado_stock_negativo=True,
            motivo_forzado="Regularización excepcional",
        )
        self.assertIsNotNone(movimiento.pk)
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("-1"))


class ControlStockNegativoVistasTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_vista_stock_negativo", "Administrador")
        self.contri = _usuario("contri_vista_stock_negativo", "Depósito")
        marca = Marca.objects.create(nombre="Marca Vista Stock Negativo")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="NEG-V",
            nombre="Producto Vista",
        )

    def test_contri_no_ve_opcion_de_forzado(self):
        self.client.login(username=self.contri.username, password="clave12345")
        response = self.client.get(reverse("stock:salida", args=[Deposito.GENERAL]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Forzar salida aunque el stock quede negativo")

    def test_diego_ve_opcion_de_forzado(self):
        self.client.login(username=self.diego.username, password="clave12345")
        response = self.client.get(reverse("stock:salida", args=[Deposito.GENERAL]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forzar salida aunque el stock quede negativo")
        self.assertContains(response, "acción excepcional y auditada")

    def test_salida_sin_stock_muestra_error_y_no_crea_movimiento(self):
        self.client.login(username=self.contri.username, password="clave12345")
        response = self.client.post(
            reverse("stock:salida", args=[Deposito.GENERAL]),
            {
                "producto": self.producto.pk,
                "cantidad": "3",
                "referencia_libre": "Obra",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuficiente")
        self.assertEqual(MovimientoStock.objects.count(), 0)

    def test_diego_puede_forzar_salida_desde_la_vista(self):
        self.client.login(username=self.diego.username, password="clave12345")
        response = self.client.post(
            reverse("stock:salida", args=[Deposito.GENERAL]),
            {
                "producto": self.producto.pk,
                "cantidad": "3",
                "referencia_libre": "Obra urgente",
                "forzar_stock_negativo": "on",
                "motivo_forzado": "Material retirado antes de registrar la compra",
            },
        )

        self.assertRedirects(response, reverse("stock:lista"))
        movimiento = MovimientoStock.objects.get(tipo=TipoMovimiento.SALIDA)
        self.assertTrue(movimiento.forzado_stock_negativo)
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("-3"))


class ControlStockNegativoTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_trabajo_stock_negativo", "Administrador")
        self.contri = _usuario("contri_trabajo_stock_negativo", "Depósito")
        marca = Marca.objects.create(nombre="Marca Trabajo Stock Negativo")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="NEG-T",
            nombre="Producto Trabajo",
        )
        cliente = Cliente.objects.create(nombre="Cliente Stock Negativo")
        presupuesto = Presupuesto.objects.create(
            cliente=cliente,
            estado=EstadoPresupuesto.ACEPTADO,
        )
        self.trabajo = Trabajo.objects.create(
            presupuesto=presupuesto,
            creado_por=self.diego,
        )
        self.material = MaterialTrabajo.objects.create(
            trabajo=self.trabajo,
            producto=self.producto,
            cantidad_necesaria=Decimal("4"),
        )

    def test_contri_ve_faltante_pero_no_puede_forzar(self):
        self.client.login(username=self.contri.username, password="clave12345")
        response = self.client.get(reverse("jobs:detalle", args=[self.trabajo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuficiente")
        self.assertNotContains(response, "Forzar envío")

    def test_diego_puede_forzar_envio_de_material(self):
        self.client.login(username=self.diego.username, password="clave12345")
        detalle = self.client.get(reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertContains(detalle, "Forzar envío")

        response = self.client.post(
            reverse("jobs:enviar_material", args=[self.material.pk]),
            {
                "forzar_stock_negativo": "on",
                "motivo_forzado": "Material ya salió físicamente del depósito",
            },
        )

        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        movimiento = MovimientoStock.objects.get(material_trabajo=self.material)
        self.assertTrue(movimiento.forzado_stock_negativo)
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("-4"))
