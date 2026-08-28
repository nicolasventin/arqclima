from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.pricing.services import costo_actual, registrar_costo
from apps.stock.models import Deposito
from apps.stock.services import stock_actual

from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra
from .permissions import puede_aprobar_orden, puede_cancelar_orden, puede_gestionar_orden
from .services import (
    TransicionInvalidaError,
    cambiar_estado_orden,
    cantidad_pendiente_recepcion,
    cantidad_recibida,
    crear_orden,
    recibir_linea,
)


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


def _proveedor(nombre="Proveedor Test"):
    return Proveedor.objects.create(nombre_comercial=nombre)


def _producto_proveedor(proveedor, codigo="COD-1"):
    marca = Marca.objects.create(nombre=f"Marca {codigo}")
    producto = Producto.objects.create(marca=marca, codigo=codigo, nombre=f"Producto {codigo}")
    return ProductoProveedor.objects.create(producto=producto, proveedor=proveedor)


class ModelConstraintTests(TestCase):
    """Garantías que viven en triggers de Postgres, no en validación de app."""

    def setUp(self):
        self.diego = _crear_usuario("diego_constraints", "Administrador")
        self.prov_a = _proveedor("Proveedor A")
        self.prov_b = _proveedor("Proveedor B")
        self.pp_a = _producto_proveedor(self.prov_a, "A-1")
        self.pp_b = _producto_proveedor(self.prov_b, "B-1")
        self.orden = crear_orden(self.prov_a, Deposito.GENERAL, self.diego)

    def test_trigger_rechaza_linea_de_otro_proveedor(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                LineaOrdenCompra.objects.create(
                    orden=self.orden, producto_proveedor=self.pp_b,
                    cantidad=Decimal("1"), costo_esperado=Decimal("100"),
                )

    def test_trigger_permite_linea_del_mismo_proveedor(self):
        linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp_a,
            cantidad=Decimal("1"), costo_esperado=Decimal("100"),
        )
        self.assertEqual(linea.orden, self.orden)

    def test_trigger_rechaza_update_a_producto_de_otro_proveedor(self):
        linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp_a,
            cantidad=Decimal("1"), costo_esperado=Decimal("100"),
        )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.producto_proveedor = self.pp_b
                linea.save()

    def test_trigger_bloquea_edicion_fuera_de_borrador(self):
        linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp_a,
            cantidad=Decimal("1"), costo_esperado=Decimal("100"),
        )
        self.orden.estado = EstadoOrdenCompra.PENDIENTE_APROBACION
        self.orden.save()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.cantidad = Decimal("2")
                linea.save()

    def test_trigger_bloquea_insert_fuera_de_borrador(self):
        self.orden.estado = EstadoOrdenCompra.PENDIENTE_APROBACION
        self.orden.save()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                LineaOrdenCompra.objects.create(
                    orden=self.orden, producto_proveedor=self.pp_a,
                    cantidad=Decimal("1"), costo_esperado=Decimal("100"),
                )

    def test_trigger_bloquea_delete_fuera_de_borrador(self):
        linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp_a,
            cantidad=Decimal("1"), costo_esperado=Decimal("100"),
        )
        self.orden.estado = EstadoOrdenCompra.APROBADA
        self.orden.save()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.delete()

    def test_permite_editar_linea_en_borrador(self):
        linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp_a,
            cantidad=Decimal("1"), costo_esperado=Decimal("100"),
        )
        linea.cantidad = Decimal("3")
        linea.save()
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad, Decimal("3"))

    def test_numero_sale_de_secuencia_postgres(self):
        otra = crear_orden(self.prov_a, Deposito.GENERAL, self.diego)
        self.assertGreater(otra.numero, self.orden.numero)
        with connection.cursor() as cur:
            cur.execute("SELECT pg_get_serial_sequence('purchasing_ordendecompra', 'numero')")
            self.assertIsNotNone(cur.fetchone()[0])


class TransicionesEstadoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_transiciones", "Administrador")
        self.orden = crear_orden(_proveedor(), Deposito.GENERAL, self.diego)

    def test_grafo_completo_de_transiciones_validas(self):
        casos = [
            (EstadoOrdenCompra.BORRADOR, EstadoOrdenCompra.PENDIENTE_APROBACION),
            (EstadoOrdenCompra.PENDIENTE_APROBACION, EstadoOrdenCompra.APROBADA),
            (EstadoOrdenCompra.APROBADA, EstadoOrdenCompra.ENVIADA),
            (EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.CANCELADA),
        ]
        orden = self.orden
        for _, destino in casos:
            cambiar_estado_orden(orden, destino, self.diego)
            orden.refresh_from_db()
            self.assertEqual(orden.estado, destino)

    def test_rechazada_vuelve_a_borrador(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.RECHAZADA, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.BORRADOR)

    def test_pendiente_aprobacion_puede_volver_a_borrador(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.BORRADOR)

    def test_cancelada_es_terminal(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.CANCELADA, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)

    def test_no_se_puede_saltar_de_borrador_a_aprobada(self):
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.APROBADA, self.diego)

    def test_no_se_puede_saltar_de_enviada_a_borrador(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.APROBADA, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.ENVIADA, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)

    def test_aprobada_puede_cancelarse(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.APROBADA, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.CANCELADA, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.CANCELADA)


class RecibirLineaTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_recibir", "Administrador")
        self.proveedor = _proveedor()
        self.pp = _producto_proveedor(self.proveedor)
        self.orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        self.linea = LineaOrdenCompra.objects.create(
            orden=self.orden, producto_proveedor=self.pp,
            cantidad=Decimal("10"), costo_esperado=Decimal("50"),
        )
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.APROBADA, self.diego)

    def test_no_se_puede_recibir_orden_en_borrador(self):
        otra_orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        otra_linea = LineaOrdenCompra.objects.create(
            orden=otra_orden, producto_proveedor=self.pp,
            cantidad=Decimal("5"), costo_esperado=Decimal("50"),
        )
        with self.assertRaises(ValueError):
            recibir_linea(otra_linea, Decimal("1"), Decimal("50"), self.diego)

    def test_cantidad_debe_ser_mayor_a_cero(self):
        with self.assertRaises(ValueError):
            recibir_linea(self.linea, Decimal("0"), Decimal("50"), self.diego)

    def test_recepcion_completa_actualiza_stock_y_costo(self):
        recibir_linea(self.linea, Decimal("10"), Decimal("55"), self.diego)

        self.assertEqual(cantidad_recibida(self.linea), Decimal("10"))
        self.assertEqual(cantidad_pendiente_recepcion(self.linea), Decimal("0"))
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("10"))

        historial = costo_actual(self.pp)
        self.assertEqual(historial.costo, Decimal("55"))
        self.assertEqual(historial.origen, "orden_compra")

    def test_recepcion_parcial_dos_veces(self):
        recibir_linea(self.linea, Decimal("6"), Decimal("50"), self.diego)
        self.assertEqual(cantidad_pendiente_recepcion(self.linea), Decimal("4"))

        recibir_linea(self.linea, Decimal("4"), Decimal("52"), self.diego)
        self.assertEqual(cantidad_recibida(self.linea), Decimal("10"))
        self.assertEqual(cantidad_pendiente_recepcion(self.linea), Decimal("0"))
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("10"))

    def test_movimiento_queda_vinculado_a_orden_y_linea(self):
        movimiento = recibir_linea(self.linea, Decimal("3"), Decimal("50"), self.diego)
        self.assertEqual(movimiento.orden_compra, self.orden)
        self.assertEqual(movimiento.linea_orden_compra, self.linea)

    def test_recepcion_permitida_tambien_en_enviada(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.ENVIADA, self.diego)
        recibir_linea(self.linea, Decimal("2"), Decimal("50"), self.diego)
        self.assertEqual(cantidad_recibida(self.linea), Decimal("2"))


class PermisosTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_permisos", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_permisos", "Ventas y Presupuestos")
        self.gabriel = _crear_usuario("gabriel_permisos", "Service y Repuestos")
        self.andres = _crear_usuario("andres_permisos", "Técnico de Campo")
        self.contri = _crear_usuario("contri_permisos", "Depósito")

    def test_solo_diego_puede_aprobar(self):
        self.assertTrue(puede_aprobar_orden(self.diego))
        for user in (self.rodrigo, self.gabriel, self.andres, self.contri):
            self.assertFalse(puede_aprobar_orden(user))

    def test_rodrigo_gabriel_andres_diego_pueden_gestionar(self):
        for user in (self.diego, self.rodrigo, self.gabriel, self.andres):
            self.assertTrue(puede_gestionar_orden(user))
        self.assertFalse(puede_gestionar_orden(self.contri))

    def test_solo_diego_puede_cancelar(self):
        self.assertTrue(puede_cancelar_orden(self.diego))
        for user in (self.rodrigo, self.gabriel, self.andres, self.contri):
            self.assertFalse(puede_cancelar_orden(user))


class ViewsTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_views", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_views", "Ventas y Presupuestos")
        self.contri = _crear_usuario("contri_views", "Depósito")
        self.proveedor = _proveedor()
        self.pp = _producto_proveedor(self.proveedor)
        registrar_costo(self.pp, Decimal("80"), self.diego)

    def test_lista_requiere_login(self):
        # PermisoRequeridoMixin usa raise_exception=True: un anónimo
        # recibe 403 directo, no un redirect al login.
        response = self.client.get(reverse("purchasing:lista"))
        self.assertEqual(response.status_code, 403)

    def test_contri_no_puede_crear_orden(self):
        self.client.login(username="contri_views", password="clave12345")
        response = self.client.post(reverse("purchasing:nueva"), {
            "proveedor": self.proveedor.pk, "deposito_destino": Deposito.GENERAL, "notas": "",
        })
        self.assertEqual(response.status_code, 403)

    def test_rodrigo_crea_orden(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        response = self.client.post(reverse("purchasing:nueva"), {
            "proveedor": self.proveedor.pk, "deposito_destino": Deposito.GENERAL, "notas": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrdenDeCompra.objects.filter(proveedor=self.proveedor).exists())

    def test_agregar_linea_prefill_costo_sugerido(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        response = self.client.get(
            reverse("purchasing:agregar_linea", args=[orden.pk]),
            {"producto_proveedor": self.pp.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial.get("costo_esperado"), Decimal("80"))

    def test_rodrigo_no_puede_aprobar(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.rodrigo)
        response = self.client.post(reverse("purchasing:aprobar", args=[orden.pk]))
        self.assertEqual(response.status_code, 403)

    def test_diego_aprueba_orden(self):
        self.client.login(username="diego_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        response = self.client.post(reverse("purchasing:aprobar", args=[orden.pk]))
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.APROBADA)

    def test_transicion_invalida_por_view_muestra_mensaje_sin_romper(self):
        self.client.login(username="diego_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        response = self.client.post(reverse("purchasing:aprobar", args=[orden.pk]))
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.BORRADOR)

    def test_contri_no_puede_recibir_deposito_ajeno(self):
        # Contri administra stock general, no repuestos.
        orden = crear_orden(self.proveedor, Deposito.REPUESTOS, self.diego)
        linea = LineaOrdenCompra.objects.create(
            orden=orden, producto_proveedor=self.pp, cantidad=Decimal("5"), costo_esperado=Decimal("80"),
        )
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(orden, EstadoOrdenCompra.APROBADA, self.diego)

        self.client.login(username="contri_views", password="clave12345")
        response = self.client.get(reverse("purchasing:recibir_linea", args=[linea.pk]))
        self.assertEqual(response.status_code, 403)

    def test_contri_recibe_en_stock_general(self):
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = LineaOrdenCompra.objects.create(
            orden=orden, producto_proveedor=self.pp, cantidad=Decimal("5"), costo_esperado=Decimal("80"),
        )
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(orden, EstadoOrdenCompra.APROBADA, self.diego)

        self.client.login(username="contri_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:recibir_linea", args=[linea.pk]),
            {"cantidad": "5", "costo_real": "82"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("5"))

    def test_no_se_puede_recibir_mas_de_lo_pendiente(self):
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = LineaOrdenCompra.objects.create(
            orden=orden, producto_proveedor=self.pp, cantidad=Decimal("5"), costo_esperado=Decimal("80"),
        )
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.diego)
        cambiar_estado_orden(orden, EstadoOrdenCompra.APROBADA, self.diego)

        self.client.login(username="diego_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:recibir_linea", args=[linea.pk]),
            {"cantidad": "6", "costo_real": "80"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No puede superar lo pendiente")
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("0"))

    def test_eliminar_linea_solo_en_borrador(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        linea = LineaOrdenCompra.objects.create(
            orden=orden, producto_proveedor=self.pp, cantidad=Decimal("5"), costo_esperado=Decimal("80"),
        )
        cambiar_estado_orden(orden, EstadoOrdenCompra.PENDIENTE_APROBACION, self.rodrigo)

        response = self.client.post(reverse("purchasing:eliminar_linea", args=[linea.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(LineaOrdenCompra.objects.filter(pk=linea.pk).exists())

    def test_detalle_muestra_filas_con_pendiente(self):
        self.client.login(username="diego_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        LineaOrdenCompra.objects.create(
            orden=orden, producto_proveedor=self.pp, cantidad=Decimal("5"), costo_esperado=Decimal("80"),
        )
        response = self.client.get(reverse("purchasing:detalle", args=[orden.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["filas"]), 1)
        self.assertEqual(response.context["filas"][0]["pendiente"], Decimal("5"))
