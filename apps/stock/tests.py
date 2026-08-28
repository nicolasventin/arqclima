from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto

from .models import Deposito, MovimientoStock, TipoMovimiento
from .permissions import puede_ajustar_stock, puede_configurar_stock_minimo, puede_registrar_entrada_salida
from .services import cantidad_pendiente_devolucion, registrar_movimiento, salidas_repuestos_pendientes, stock_actual


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


def _producto(codigo, es_repuesto=False):
    marca, _ = Marca.objects.get_or_create(nombre="Marca Stock Test")
    return Producto.objects.create(marca=marca, codigo=codigo, nombre=f"Producto {codigo}", es_repuesto=es_repuesto)


class StockActualTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_stock_actual", "Administrador")
        self.producto = _producto("S1")

    def test_stock_actual_sin_movimientos_es_cero(self):
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("0"))

    def test_entrada_suma_y_salida_resta(self):
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"), usuario=self.diego,
        )
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-3"), usuario=self.diego,
        )
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("7"))

    def test_depositos_son_independientes(self):
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"), usuario=self.diego,
        )
        self.assertEqual(stock_actual(self.producto, Deposito.REPUESTOS), Decimal("0"))

    def test_ajuste_puede_ser_negativo(self):
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"), usuario=self.diego,
        )
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.AJUSTE,
            cantidad=Decimal("-2"), usuario=self.diego, referencia_libre="Rotura",
        )
        self.assertEqual(stock_actual(self.producto, Deposito.GENERAL), Decimal("8"))


class ValidacionServicioTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_validacion", "Administrador")
        self.producto = _producto("S2", es_repuesto=True)

    def test_entrada_con_cantidad_negativa_falla(self):
        with self.assertRaises(ValueError):
            registrar_movimiento(
                producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
                cantidad=Decimal("-5"), usuario=self.diego,
            )

    def test_salida_con_cantidad_positiva_falla(self):
        with self.assertRaises(ValueError):
            registrar_movimiento(
                producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("5"), usuario=self.diego,
            )

    def test_requiere_devolucion_fuera_de_salida_repuestos_falla(self):
        with self.assertRaises(ValueError):
            registrar_movimiento(
                producto=self.producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-5"), usuario=self.diego, requiere_devolucion=True,
            )


class ConstraintsBaseDeDatosTests(TestCase):
    """
    Igual criterio que HistorialCosto: la garantía de signo/coherencia
    vive en la base (CheckConstraint), no solo en el servicio.
    """

    def setUp(self):
        self.producto = _producto("S3")

    def test_signo_incoherente_con_tipo_falla_en_la_base(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto, deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.ENTRADA, cantidad=Decimal("-5"),
                )

    def test_requiere_devolucion_fuera_de_salida_repuestos_falla_en_la_base(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto, deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.SALIDA, cantidad=Decimal("-5"), requiere_devolucion=True,
                )


class MovimientoInmutableTests(TestCase):
    """El trigger de Postgres es el resguardo real, mismo patrón que HistorialCosto."""

    def setUp(self):
        producto = _producto("S4")
        diego = _crear_usuario("diego_inmutable", "Administrador")
        self.movimiento = registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"), usuario=diego,
        )

    def test_no_se_puede_actualizar_via_sql_directo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE stock_movimientostock SET cantidad = 999 WHERE id = %s",
                        [self.movimiento.id],
                    )

    def test_no_se_puede_borrar_via_sql_directo(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM stock_movimientostock WHERE id = %s", [self.movimiento.id]
                    )
        self.assertTrue(MovimientoStock.objects.filter(pk=self.movimiento.pk).exists())


class PendienteDevolucionTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_devolucion", "Administrador")
        self.producto = _producto("S5", es_repuesto=True)

    def test_salida_sin_requiere_devolucion_no_aparece_en_pendientes(self):
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego,
        )
        self.assertEqual(salidas_repuestos_pendientes(), [])

    def test_salida_con_requiere_devolucion_queda_pendiente(self):
        salida = registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego, requiere_devolucion=True,
        )
        self.assertEqual(cantidad_pendiente_devolucion(salida), Decimal("5"))
        self.assertEqual(salidas_repuestos_pendientes(), [salida])

    def test_devolucion_parcial_reduce_lo_pendiente_pero_sigue_pendiente(self):
        salida = registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego, requiere_devolucion=True,
        )
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.DEVOLUCION,
            cantidad=Decimal("2"), usuario=self.diego, salida_relacionada=salida,
        )
        self.assertEqual(cantidad_pendiente_devolucion(salida), Decimal("3"))
        self.assertEqual(salidas_repuestos_pendientes(), [salida])
        self.assertEqual(stock_actual(self.producto, Deposito.REPUESTOS), Decimal("-3"))

    def test_devolucion_completa_saca_de_pendientes(self):
        salida = registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego, requiere_devolucion=True,
        )
        registrar_movimiento(
            producto=self.producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.DEVOLUCION,
            cantidad=Decimal("5"), usuario=self.diego, salida_relacionada=salida,
        )
        self.assertEqual(cantidad_pendiente_devolucion(salida), Decimal("0"))
        self.assertEqual(salidas_repuestos_pendientes(), [])


class PermisosStockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_permisos_stock", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_permisos_stock", "Ventas y Presupuestos")
        cls.gabriel = _crear_usuario("gabriel_permisos_stock", "Service y Repuestos")
        cls.contri = _crear_usuario("contri_permisos_stock", "Depósito")
        cls.andres = _crear_usuario("andres_permisos_stock", "Técnico de Campo")

    def test_diego_puede_todo(self):
        self.assertTrue(puede_registrar_entrada_salida(self.diego, Deposito.GENERAL))
        self.assertTrue(puede_registrar_entrada_salida(self.diego, Deposito.REPUESTOS))
        self.assertTrue(puede_ajustar_stock(self.diego, Deposito.GENERAL))
        self.assertTrue(puede_configurar_stock_minimo(self.diego))

    def test_rodrigo_no_puede_registrar_nada(self):
        self.assertFalse(puede_registrar_entrada_salida(self.rodrigo, Deposito.GENERAL))
        self.assertFalse(puede_registrar_entrada_salida(self.rodrigo, Deposito.REPUESTOS))
        self.assertFalse(puede_ajustar_stock(self.rodrigo, Deposito.GENERAL))

    def test_gabriel_solo_repuestos(self):
        self.assertTrue(puede_registrar_entrada_salida(self.gabriel, Deposito.REPUESTOS))
        self.assertFalse(puede_registrar_entrada_salida(self.gabriel, Deposito.GENERAL))
        self.assertFalse(puede_ajustar_stock(self.gabriel, Deposito.GENERAL))

    def test_contri_general_con_ajuste(self):
        self.assertTrue(puede_registrar_entrada_salida(self.contri, Deposito.GENERAL))
        self.assertFalse(puede_registrar_entrada_salida(self.contri, Deposito.REPUESTOS))
        self.assertTrue(puede_ajustar_stock(self.contri, Deposito.GENERAL))

    def test_andres_general_sin_ajuste(self):
        self.assertTrue(puede_registrar_entrada_salida(self.andres, Deposito.GENERAL))
        self.assertFalse(puede_registrar_entrada_salida(self.andres, Deposito.REPUESTOS))
        self.assertFalse(puede_ajustar_stock(self.andres, Deposito.GENERAL))

    def test_solo_diego_configura_stock_minimo(self):
        self.assertFalse(puede_configurar_stock_minimo(self.contri))
        self.assertFalse(puede_configurar_stock_minimo(self.gabriel))


class StockViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_vistas_stock", "Administrador")
        cls.contri = _crear_usuario("contri_vistas_stock", "Depósito")
        cls.gabriel = _crear_usuario("gabriel_vistas_stock", "Service y Repuestos")
        cls.andres = _crear_usuario("andres_vistas_stock", "Técnico de Campo")

    def test_todos_pueden_ver_la_lista_de_stock(self):
        for username in ("diego_vistas_stock", "contri_vistas_stock", "gabriel_vistas_stock"):
            self.client.login(username=username, password="clave12345")
            response = self.client.get(reverse("stock:lista"))
            self.assertEqual(response.status_code, 200, username)
            self.client.logout()

    def test_contri_puede_registrar_entrada_general(self):
        producto = _producto("V1")
        self.client.login(username="contri_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:entrada", args=["general"]),
            {"producto": producto.pk, "cantidad": "10", "referencia_libre": ""},
        )
        self.assertRedirects(response, reverse("stock:lista"))
        self.assertEqual(stock_actual(producto, Deposito.GENERAL), Decimal("10"))

    def test_contri_no_puede_registrar_entrada_repuestos(self):
        producto = _producto("V2", es_repuesto=True)
        self.client.login(username="contri_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:entrada", args=["repuestos"]),
            {"producto": producto.pk, "cantidad": "10", "referencia_libre": ""},
        )
        self.assertEqual(response.status_code, 403)

    def test_gabriel_puede_registrar_salida_repuestos_con_devolucion(self):
        producto = _producto("V3", es_repuesto=True)
        registrar_movimiento(
            producto=producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("20"), usuario=self.diego,
        )
        self.client.login(username="gabriel_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:salida", args=["repuestos"]),
            {
                "producto": producto.pk, "cantidad": "5", "referencia_libre": "Service AC Pérez",
                "requiere_devolucion": "on",
            },
        )
        self.assertRedirects(response, reverse("stock:lista"))
        salida = MovimientoStock.objects.get(producto=producto, tipo=TipoMovimiento.SALIDA)
        self.assertTrue(salida.requiere_devolucion)
        self.assertEqual(salidas_repuestos_pendientes(), [salida])

    def test_andres_puede_registrar_entrada_y_salida_general(self):
        producto = _producto("V4")
        self.client.login(username="andres_vistas_stock", password="clave12345")
        r1 = self.client.post(
            reverse("stock:entrada", args=["general"]),
            {"producto": producto.pk, "cantidad": "5", "referencia_libre": "Sobrante obra"},
        )
        self.assertEqual(r1.status_code, 302)
        r2 = self.client.post(
            reverse("stock:salida", args=["general"]),
            {"producto": producto.pk, "cantidad": "2", "referencia_libre": ""},
        )
        self.assertEqual(r2.status_code, 302)

    def test_andres_no_puede_ajustar(self):
        self.client.login(username="andres_vistas_stock", password="clave12345")
        response = self.client.get(reverse("stock:ajuste"))
        self.assertEqual(response.status_code, 403)

    def test_registrar_devolucion_via_vista_no_puede_superar_lo_pendiente(self):
        producto = _producto("V5", es_repuesto=True)
        registrar_movimiento(
            producto=producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("20"), usuario=self.diego,
        )
        salida = registrar_movimiento(
            producto=producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.gabriel, requiere_devolucion=True,
        )
        self.client.login(username="gabriel_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:registrar_devolucion", args=[salida.pk]), {"cantidad": "10"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cantidad_pendiente_devolucion(salida), Decimal("5"))

    def test_configurar_stock_minimo_solo_diego(self):
        producto = _producto("V6")
        self.client.login(username="contri_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:actualizar_stock_minimo", args=[producto.pk]),
            {"stock_minimo_general": "5", "stock_minimo_repuestos": ""},
        )
        self.assertEqual(response.status_code, 403)

        self.client.login(username="diego_vistas_stock", password="clave12345")
        response = self.client.post(
            reverse("stock:actualizar_stock_minimo", args=[producto.pk]),
            {"stock_minimo_general": "5", "stock_minimo_repuestos": ""},
        )
        self.assertRedirects(response, reverse("catalog:producto_detalle", args=[producto.pk]))
        producto.refresh_from_db()
        self.assertEqual(producto.stock_minimo_general, Decimal("5.00"))
