import threading
from decimal import Decimal
from unittest.mock import patch

from django.db import close_old_connections
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.clients.models import Cliente
from apps.jobs.models import MaterialTrabajo, Trabajo
from apps.jobs.services import (
    cantidad_enviada,
    enviar_material,
    generar_listado_materiales,
)
from apps.pricing.models import HistorialCosto
from apps.purchasing.models import (
    EstadoOrdenCompra,
    LineaOrdenCompra,
    OrdenDeCompra,
)
from apps.purchasing.services import (
    cambiar_estado_orden,
    cantidad_recibida,
    recibir_linea,
)
from apps.quotes.models import (
    EstadoPresupuesto,
    ItemPresupuesto,
    Presupuesto,
    SeccionPresupuesto,
)
from apps.quotes.services import cambiar_estado
from apps.stock.models import Deposito, MovimientoStock, TipoMovimiento
from apps.stock.services import (
    StockInsuficienteError,
    cantidad_pendiente_devolucion,
    registrar_devolucion,
    registrar_movimiento,
)


class _ConcurrenteMixin:
    def ejecutar_dos(self, preparar):
        """
        Cada hilo prepara sus objetos ORM antes de la barrera. Así ambos
        llegan al servicio con una lectura potencialmente vieja, igual que
        dos requests HTTP que se abrieron al mismo tiempo.
        """
        barrera = threading.Barrier(2)
        resultados = []
        resultados_lock = threading.Lock()

        def worker():
            close_old_connections()
            operacion = preparar()
            barrera.wait(timeout=5)
            try:
                operacion()
            except Exception as exc:  # el tipo exacto se verifica en cada test
                resultado = exc
            else:
                resultado = None
            finally:
                close_old_connections()

            with resultados_lock:
                resultados.append(resultado)

        hilos = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=15)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos), "Un hilo quedó bloqueado.")
        self.assertEqual(len(resultados), 2)
        return resultados


class ConcurrenciaOperacionesCriticasTests(_ConcurrenteMixin, TransactionTestCase):

    def setUp(self):
        self.usuario = User.objects.create_user(
            username=f"concurrencia_{self._testMethodName}",
            password="clave12345",
        )
        self.usuario.groups.add(Group.objects.get(name="Administrador"))
        self.marca = Marca.objects.create(nombre=f"Marca {self._testMethodName}")
        self.proveedor = Proveedor.objects.create(
            nombre_comercial=f"Proveedor {self._testMethodName}"
        )
        self.producto = Producto.objects.create(
            marca=self.marca,
            codigo=f"C-{self._testMethodName[:20]}",
            nombre=f"Producto {self._testMethodName}",
            es_repuesto=True,
        )
        self.producto_proveedor = ProductoProveedor.objects.create(
            producto=self.producto,
            proveedor=self.proveedor,
        )

    def test_dos_recepciones_simultaneas_no_sobre_reciben(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
            creado_por=self.usuario,
        )
        linea = LineaOrdenCompra.objects.create(
            orden=orden,
            producto_proveedor=self.producto_proveedor,
            cantidad=Decimal("10"),
            costo_esperado=Decimal("100"),
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.PENDIENTE_APROBACION,
            self.usuario,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.APROBADA,
            self.usuario,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            self.usuario,
        )
        orden.refresh_from_db()

        def preparar():
            linea_local = LineaOrdenCompra.objects.get(pk=linea.pk)
            usuario_local = User.objects.get(pk=self.usuario.pk)
            return lambda: recibir_linea(
                linea_local,
                Decimal("10"),
                Decimal("110"),
                usuario_local,
            )

        resultados = self.ejecutar_dos(preparar)

        self.assertEqual(sum(resultado is None for resultado in resultados), 1)
        self.assertEqual(sum(isinstance(resultado, ValueError) for resultado in resultados), 1)
        linea.refresh_from_db()
        self.assertEqual(cantidad_recibida(linea), Decimal("10"))
        self.assertEqual(
            MovimientoStock.objects.filter(linea_orden_compra=linea).count(),
            1,
        )
        self.assertEqual(
            HistorialCosto.objects.filter(
                producto_proveedor=self.producto_proveedor,
                origen="orden_compra",
            ).count(),
            1,
        )

    def test_dos_envios_simultaneos_no_duplican_material(self):
        cliente = Cliente.objects.create(nombre="Cliente concurrencia envío")
        presupuesto = Presupuesto.objects.create(
            cliente=cliente,
            estado=EstadoPresupuesto.ACEPTADO,
        )
        trabajo = Trabajo.objects.create(
            presupuesto=presupuesto,
            creado_por=self.usuario,
        )
        material = MaterialTrabajo.objects.create(
            trabajo=trabajo,
            producto=self.producto,
            cantidad_necesaria=Decimal("5"),
        )
        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"),
            usuario=self.usuario,
        )

        def preparar():
            material_local = MaterialTrabajo.objects.get(pk=material.pk)
            usuario_local = User.objects.get(pk=self.usuario.pk)
            return lambda: enviar_material(material_local, usuario_local)

        resultados = self.ejecutar_dos(preparar)

        self.assertEqual(sum(resultado is None for resultado in resultados), 1)
        self.assertEqual(sum(isinstance(resultado, ValueError) for resultado in resultados), 1)
        material.refresh_from_db()
        self.assertEqual(cantidad_enviada(material), Decimal("5"))
        self.assertEqual(
            MovimientoStock.objects.filter(material_trabajo=material).count(),
            1,
        )

    def test_dos_salidas_simultaneas_no_pueden_consumir_el_mismo_stock(self):
        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"),
            usuario=self.usuario,
        )

        def preparar():
            producto_local = Producto.objects.get(pk=self.producto.pk)
            usuario_local = User.objects.get(pk=self.usuario.pk)
            return lambda: registrar_movimiento(
                producto=producto_local,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-4"),
                usuario=usuario_local,
            )

        resultados = self.ejecutar_dos(preparar)

        self.assertEqual(sum(resultado is None for resultado in resultados), 1)
        self.assertEqual(
            sum(isinstance(resultado, StockInsuficienteError) for resultado in resultados),
            1,
        )
        total = MovimientoStock.objects.filter(
            producto=self.producto,
            deposito=Deposito.GENERAL,
        ).aggregate(total=Sum("cantidad"))["total"]
        self.assertEqual(total, Decimal("1"))

    def test_dos_devoluciones_simultaneas_no_superan_lo_pendiente(self):
        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.REPUESTOS,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"),
            usuario=self.usuario,
        )
        salida = registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.REPUESTOS,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"),
            usuario=self.usuario,
            requiere_devolucion=True,
        )

        def preparar():
            salida_local = MovimientoStock.objects.get(pk=salida.pk)
            usuario_local = User.objects.get(pk=self.usuario.pk)
            return lambda: registrar_devolucion(
                salida_local,
                Decimal("5"),
                usuario_local,
            )

        resultados = self.ejecutar_dos(preparar)

        self.assertEqual(sum(resultado is None for resultado in resultados), 1)
        self.assertEqual(sum(isinstance(resultado, ValueError) for resultado in resultados), 1)
        salida.refresh_from_db()
        self.assertEqual(cantidad_pendiente_devolucion(salida), Decimal("0"))
        self.assertEqual(salida.devoluciones.count(), 1)

    def test_dos_transiciones_simultaneas_no_usan_estado_viejo(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
            estado=EstadoOrdenCompra.BORRADOR,
            creado_por=self.usuario,
        )
        LineaOrdenCompra.objects.create(
            orden=orden,
            producto_proveedor=self.producto_proveedor,
            cantidad=Decimal("1"),
            costo_esperado=Decimal("100"),
        )

        def preparar():
            orden_local = OrdenDeCompra.objects.get(pk=orden.pk)
            usuario_local = User.objects.get(pk=self.usuario.pk)
            return lambda: cambiar_estado_orden(
                orden_local,
                EstadoOrdenCompra.PENDIENTE_APROBACION,
                usuario_local,
            )

        resultados = self.ejecutar_dos(preparar)

        self.assertEqual(sum(resultado is None for resultado in resultados), 1)
        self.assertEqual(sum(isinstance(resultado, ValueError) for resultado in resultados), 1)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.PENDIENTE_APROBACION)
        self.assertEqual(
            AuditLog.objects.filter(
                accion="solicitar_aprobacion_orden_compra",
                object_id=str(orden.pk),
            ).count(),
            1,
        )


class AtomicidadOperacionesCriticasTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username=f"atomicidad_{self._testMethodName}",
            password="clave12345",
        )
        self.usuario.groups.add(Group.objects.get(name="Administrador"))
        self.marca = Marca.objects.create(nombre=f"Marca A {self._testMethodName}")
        self.proveedor = Proveedor.objects.create(
            nombre_comercial=f"Proveedor A {self._testMethodName}"
        )
        self.producto = Producto.objects.create(
            marca=self.marca,
            codigo=f"A-{self._testMethodName[:20]}",
            nombre=f"Producto A {self._testMethodName}",
        )
        self.producto_proveedor = ProductoProveedor.objects.create(
            producto=self.producto,
            proveedor=self.proveedor,
        )

    def test_movimiento_y_auditoria_son_una_sola_transaccion(self):
        with patch(
            "apps.stock.services.log_action",
            side_effect=RuntimeError("auditoría caída"),
        ):
            with self.assertRaises(RuntimeError):
                registrar_movimiento(
                    producto=self.producto,
                    deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.ENTRADA,
                    cantidad=Decimal("5"),
                    usuario=self.usuario,
                )

        self.assertEqual(MovimientoStock.objects.count(), 0)

    def test_recepcion_revierte_costo_si_falla_el_movimiento(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
            creado_por=self.usuario,
        )
        linea = LineaOrdenCompra.objects.create(
            orden=orden,
            producto_proveedor=self.producto_proveedor,
            cantidad=Decimal("5"),
            costo_esperado=Decimal("100"),
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.PENDIENTE_APROBACION,
            self.usuario,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.APROBADA,
            self.usuario,
        )
        cambiar_estado_orden(
            orden,
            EstadoOrdenCompra.ENVIADA,
            self.usuario,
        )
        orden.refresh_from_db()

        with patch(
            "apps.purchasing.services.registrar_movimiento",
            side_effect=RuntimeError("stock caído"),
        ):
            with self.assertRaises(RuntimeError):
                recibir_linea(
                    linea,
                    Decimal("5"),
                    Decimal("120"),
                    self.usuario,
                )

        self.assertEqual(
            HistorialCosto.objects.filter(
                producto_proveedor=self.producto_proveedor,
                origen="orden_compra",
            ).count(),
            0,
        )
        self.assertEqual(
            MovimientoStock.objects.filter(linea_orden_compra=linea).count(),
            0,
        )

    def test_listado_materiales_no_queda_a_mitad_si_falla_auditoria(self):
        cliente = Cliente.objects.create(nombre="Cliente atomicidad listado")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        seccion = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="Etapa 1",
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=seccion,
            producto=self.producto,
            cantidad=Decimal("2"),
            precio_unitario=Decimal("100"),
        )
        Presupuesto.objects.filter(pk=presupuesto.pk).update(
            estado=EstadoPresupuesto.ACEPTADO
        )
        presupuesto.refresh_from_db()
        trabajo = Trabajo.objects.create(
            presupuesto=presupuesto,
            creado_por=self.usuario,
        )

        with patch(
            "apps.jobs.services.log_action",
            side_effect=RuntimeError("auditoría caída"),
        ):
            with self.assertRaises(RuntimeError):
                generar_listado_materiales(trabajo, self.usuario)

        self.assertEqual(trabajo.etapas.count(), 0)
        self.assertEqual(trabajo.materiales.count(), 0)

    def test_transicion_revierte_estado_si_falla_auditoria(self):
        cliente = Cliente.objects.create(nombre="Cliente atomicidad estado")
        presupuesto = Presupuesto.objects.create(cliente=cliente)

        with patch(
            "apps.quotes.services.log_action",
            side_effect=RuntimeError("auditoría caída"),
        ):
            with self.assertRaises(RuntimeError):
                cambiar_estado(
                    presupuesto,
                    EstadoPresupuesto.CANCELADO,
                    self.usuario,
                )

        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.BORRADOR)
