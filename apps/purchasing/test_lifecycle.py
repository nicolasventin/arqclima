from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.stock.models import Deposito

from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra
from .services import (
    TransicionInvalidaError,
    cambiar_estado_orden,
    cantidad_pendiente_recepcion,
    cerrar_orden,
    crear_orden,
    recibir_linea,
)


def _usuario(username, rol):
    grupo = Group.objects.get(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class CicloOrdenCompraTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_10e", "Administrador")
        self.rodrigo = _usuario("rodrigo_10e", "Ventas y Presupuestos")
        self.contri = _usuario("contri_10e", "Depósito")
        self.proveedor = Proveedor.objects.create(nombre_comercial="Proveedor 10E")
        self.marca = Marca.objects.create(nombre="Marca 10E")
        self.producto_a = Producto.objects.create(
            marca=self.marca,
            codigo="10E-A",
            nombre="Producto A 10E",
        )
        self.producto_b = Producto.objects.create(
            marca=self.marca,
            codigo="10E-B",
            nombre="Producto B 10E",
        )
        self.pp_a = ProductoProveedor.objects.create(
            producto=self.producto_a,
            proveedor=self.proveedor,
        )
        self.pp_b = ProductoProveedor.objects.create(
            producto=self.producto_b,
            proveedor=self.proveedor,
        )

    def _orden(self, creador=None, dos_lineas=False):
        creador = creador or self.diego
        orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            creador,
        )
        linea_a = LineaOrdenCompra.objects.create(
            orden=orden,
            producto_proveedor=self.pp_a,
            cantidad=Decimal("10"),
            costo_esperado=Decimal("50"),
        )
        linea_b = None
        if dos_lineas:
            linea_b = LineaOrdenCompra.objects.create(
                orden=orden,
                producto_proveedor=self.pp_b,
                cantidad=Decimal("4"),
                costo_esperado=Decimal("80"),
            )
        return orden, linea_a, linea_b

    def _enviada(self, dos_lineas=False, creador=None):
        orden, linea_a, linea_b = self._orden(
            creador=creador,
            dos_lineas=dos_lineas,
        )
        actor = creador or self.diego
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            actor,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            actor,
        )
        orden.refresh_from_db()
        return orden, linea_a, linea_b

    def test_no_se_puede_emitir_sin_lineas(self):
        orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            self.rodrigo,
        )

        with self.assertRaisesMessage(ValueError, "sin líneas"):
            cambiar_estado_orden(
                orden,
                EstadoOrdenCompra.EMITIDA,
                self.rodrigo,
            )

        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.BORRADOR)

    def test_rodrigo_puede_emitir_sin_aprobacion_de_diego(self):
        orden, _, _ = self._orden(creador=self.rodrigo)
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.rodrigo,
        )
        orden.refresh_from_db()

        self.assertEqual(orden.estado, EstadoOrdenCompra.EMITIDA)
        self.assertEqual(orden.emitida_por, self.rodrigo)
        self.assertIsNotNone(orden.emitida_en)

    def test_emitida_puede_reabrirse_a_borrador(self):
        orden, _, _ = self._orden(creador=self.rodrigo)
        cambiar_estado_orden(orden, EstadoOrdenCompra.EMITIDA, self.rodrigo)
        cambiar_estado_orden(orden, EstadoOrdenCompra.BORRADOR, self.rodrigo)
        orden.refresh_from_db()

        self.assertEqual(orden.estado, EstadoOrdenCompra.BORRADOR)
        self.assertIsNone(orden.emitida_por)
        self.assertIsNone(orden.emitida_en)

    def test_cancelacion_requiere_motivo_y_lo_guarda(self):
        orden, _, _ = self._orden()
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.diego,
        )

        with self.assertRaisesMessage(ValueError, "motivo de la cancelación"):
            cambiar_estado_orden(
                orden,
                EstadoOrdenCompra.CANCELADA,
                self.diego,
            )

        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.CANCELADA,
            self.diego,
            motivo="Proveedor sin disponibilidad",
        )
        orden.refresh_from_db()

        self.assertEqual(orden.cancelada_por, self.diego)
        self.assertIsNotNone(orden.cancelada_en)
        self.assertEqual(
            orden.motivo_cancelacion,
            "Proveedor sin disponibilidad",
        )

    def test_marcar_enviada_guarda_actor_y_fecha(self):
        orden, _, _ = self._orden(creador=self.rodrigo)
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.rodrigo,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            self.rodrigo,
        )
        orden.refresh_from_db()

        self.assertEqual(orden.enviada_por, self.rodrigo)
        self.assertIsNotNone(orden.enviada_en)

    def test_primera_recepcion_mueve_a_recepcion_parcial(self):
        orden, linea, _ = self._enviada()

        recibir_linea(
            linea,
            Decimal("3"),
            Decimal("52"),
            self.contri,
        )
        orden.refresh_from_db()

        self.assertEqual(
            orden.estado,
            EstadoOrdenCompra.RECEPCION_PARCIAL,
        )
        self.assertIsNotNone(orden.primera_recepcion_en)
        self.assertIsNone(orden.recibida_en)
        self.assertEqual(
            cantidad_pendiente_recepcion(linea),
            Decimal("7"),
        )

    def test_recepcion_completa_mueve_automaticamente_a_recibida(self):
        orden, linea, _ = self._enviada()

        recibir_linea(
            linea,
            Decimal("10"),
            Decimal("52"),
            self.contri,
        )
        orden.refresh_from_db()

        self.assertEqual(orden.estado, EstadoOrdenCompra.RECIBIDA)
        self.assertIsNotNone(orden.primera_recepcion_en)
        self.assertIsNotNone(orden.recibida_en)

    def test_dos_lineas_no_quedan_recibidas_hasta_completar_ambas(self):
        orden, linea_a, linea_b = self._enviada(dos_lineas=True)

        recibir_linea(
            linea_a,
            Decimal("10"),
            Decimal("50"),
            self.contri,
        )
        orden.refresh_from_db()
        self.assertEqual(
            orden.estado,
            EstadoOrdenCompra.RECEPCION_PARCIAL,
        )

        recibir_linea(
            linea_b,
            Decimal("4"),
            Decimal("80"),
            self.contri,
        )
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.RECIBIDA)

    def test_no_se_puede_cancelar_despues_de_una_recepcion(self):
        orden, linea, _ = self._enviada()
        recibir_linea(
            linea,
            Decimal("2"),
            Decimal("50"),
            self.contri,
        )

        with self.assertRaises((TransicionInvalidaError, ValueError)):
            cambiar_estado_orden(
                orden,
                EstadoOrdenCompra.CANCELADA,
                self.diego,
                motivo="Intento inválido",
            )

        orden.refresh_from_db()
        self.assertEqual(
            orden.estado,
            EstadoOrdenCompra.RECEPCION_PARCIAL,
        )

    def test_cierre_parcial_requiere_motivo_y_conserva_pendiente(self):
        orden, linea, _ = self._enviada()
        recibir_linea(
            linea,
            Decimal("6"),
            Decimal("50"),
            self.contri,
        )

        with self.assertRaisesMessage(
            ValueError,
            "mercadería pendiente",
        ):
            cerrar_orden(orden, self.diego)

        cerrar_orden(
            orden,
            self.diego,
            motivo="Proveedor entregó solo seis unidades",
        )
        orden.refresh_from_db()

        self.assertEqual(orden.estado, EstadoOrdenCompra.CERRADA)
        self.assertEqual(orden.cerrada_por, self.diego)
        self.assertIsNotNone(orden.cerrada_en)
        self.assertEqual(
            orden.motivo_cierre,
            "Proveedor entregó solo seis unidades",
        )
        self.assertEqual(
            cantidad_pendiente_recepcion(linea),
            Decimal("4"),
        )

    def test_orden_recibida_puede_cerrarse_sin_motivo(self):
        orden, linea, _ = self._enviada()
        recibir_linea(
            linea,
            Decimal("10"),
            Decimal("50"),
            self.contri,
        )

        cerrar_orden(orden, self.diego)
        orden.refresh_from_db()

        self.assertEqual(orden.estado, EstadoOrdenCompra.CERRADA)
        self.assertEqual(orden.motivo_cierre, "")

    def test_rodrigo_no_puede_cerrar(self):
        orden, linea, _ = self._enviada(creador=self.rodrigo)
        recibir_linea(
            linea,
            Decimal("10"),
            Decimal("50"),
            self.contri,
        )

        with self.assertRaises(PermissionError):
            cerrar_orden(orden, self.rodrigo)

    def test_auditoria_distingue_emision_y_envio(self):
        orden, _, _ = self._orden(creador=self.rodrigo)
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.rodrigo,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            self.rodrigo,
        )

        acciones = list(
            AuditLog.objects.filter(
                object_id=str(orden.pk),
            ).values_list("accion", flat=True)
        )
        self.assertIn("emitir_orden_compra", acciones)
        self.assertIn("enviar_orden_compra_proveedor", acciones)
        self.assertNotIn("aprobar_orden_compra", acciones)


class TriggerCicloOrdenCompraTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_trigger_10e", "Administrador")
        self.proveedor = Proveedor.objects.create(
            nombre_comercial="Proveedor Trigger 10E"
        )
        marca = Marca.objects.create(nombre="Marca Trigger 10E")
        producto = Producto.objects.create(
            marca=marca,
            codigo="10E-TR",
            nombre="Producto Trigger 10E",
        )
        self.pp = ProductoProveedor.objects.create(
            producto=producto,
            proveedor=self.proveedor,
        )

    def _orden_con_linea(self):
        orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            self.diego,
        )
        LineaOrdenCompra.objects.create(
            orden=orden,
            producto_proveedor=self.pp,
            cantidad=Decimal("2"),
            costo_esperado=Decimal("10"),
        )
        return orden

    def test_db_rechaza_salto_de_borrador_a_enviada(self):
        orden = self._orden_con_linea()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrdenDeCompra.objects.filter(pk=orden.pk).update(
                    estado=EstadoOrdenCompra.ENVIADA
                )

    def test_db_rechaza_emitida_sin_metadatos(self):
        orden = self._orden_con_linea()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrdenDeCompra.objects.filter(pk=orden.pk).update(
                    estado=EstadoOrdenCompra.EMITIDA
                )

    def test_db_rechaza_recibida_sin_movimientos_reales(self):
        orden = self._orden_con_linea()
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.diego,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            self.diego,
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrdenDeCompra.objects.filter(pk=orden.pk).update(
                    estado=EstadoOrdenCompra.RECIBIDA
                )

    def test_db_rechaza_cancelacion_sin_metadatos(self):
        orden = self._orden_con_linea()
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.EMITIDA,
            self.diego,
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                OrdenDeCompra.objects.filter(pk=orden.pk).update(
                    estado=EstadoOrdenCompra.CANCELADA
                )


class VistasCicloOrdenCompraTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_view_10e", "Administrador")
        self.contri = _usuario("contri_view_10e", "Depósito")
        self.proveedor = Proveedor.objects.create(
            nombre_comercial="Proveedor View 10E"
        )
        marca = Marca.objects.create(nombre="Marca View 10E")
        producto = Producto.objects.create(
            marca=marca,
            codigo="10E-V",
            nombre="Producto View 10E",
        )
        self.pp = ProductoProveedor.objects.create(
            producto=producto,
            proveedor=self.proveedor,
        )
        self.orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            self.diego,
        )
        self.linea = LineaOrdenCompra.objects.create(
            orden=self.orden,
            producto_proveedor=self.pp,
            cantidad=Decimal("5"),
            costo_esperado=Decimal("20"),
        )
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.EMITIDA,
            self.diego,
        )

    def test_no_hay_recepcion_directa_mientras_esta_solo_emitida(self):
        self.client.login(
            username=self.contri.username,
            password="clave12345",
        )

        response = self.client.get(
            reverse(
                "purchasing:recibir_linea",
                args=[self.linea.pk],
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_detalle_muestra_emision_hitos_y_estado_parcial(self):
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.ENVIADA,
            self.diego,
        )
        recibir_linea(
            self.linea,
            Decimal("2"),
            Decimal("20"),
            self.contri,
        )

        self.client.login(
            username=self.diego.username,
            password="clave12345",
        )
        response = self.client.get(
            reverse(
                "purchasing:detalle",
                args=[self.orden.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Emitida")
        self.assertContains(response, "Recepción parcial")
        self.assertContains(response, "Primera recepción")
        self.assertContains(response, "Motivo obligatorio del cierre parcial")
        self.assertNotContains(response, "Aprobada")

    def test_cierre_parcial_desde_vista_exige_motivo(self):
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.ENVIADA,
            self.diego,
        )
        recibir_linea(
            self.linea,
            Decimal("2"),
            Decimal("20"),
            self.contri,
        )

        self.client.login(
            username=self.diego.username,
            password="clave12345",
        )
        response = self.client.post(
            reverse(
                "purchasing:cerrar",
                args=[self.orden.pk],
            ),
            {"motivo": "Proveedor no entregará el resto"},
        )

        self.assertRedirects(
            response,
            reverse(
                "purchasing:detalle",
                args=[self.orden.pk],
            ),
        )
        self.orden.refresh_from_db()
        self.assertEqual(
            self.orden.estado,
            EstadoOrdenCompra.CERRADA,
        )
        self.assertEqual(
            self.orden.motivo_cierre,
            "Proveedor no entregará el resto",
        )
