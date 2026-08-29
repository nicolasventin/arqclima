from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto
from apps.clients.models import Cliente
from apps.jobs.models import EstadoTrabajo
from apps.jobs.services import cambiar_estado_trabajo, crear_trabajo, finalizar_trabajo
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto
from apps.stock.models import Deposito, TipoMovimiento
from apps.stock.services import registrar_movimiento


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class HomeViewWidgetsTests(TestCase):
    """
    Widgets sumados al cerrar la Etapa 9 (antes solo estaba "Mis
    tareas"). Cada uno se gatea con el mismo permiso que su reporte
    correspondiente — estos tests verifican esa correspondencia, no
    solo que el número salga bien.
    """

    def setUp(self):
        self.diego = _crear_usuario("diego_dashboard", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_dashboard", "Ventas y Presupuestos")
        self.contri = _crear_usuario("contri_dashboard", "Depósito")
        self.gabriel = _crear_usuario("gabriel_dashboard", "Service y Repuestos")
        self.andres = _crear_usuario("andres_dashboard", "Técnico de Campo")
        self.cliente = Cliente.objects.create(nombre="Cliente Dashboard")

    def _login(self, username):
        self.client.login(username=username, password="clave12345")

    def test_diego_ve_los_tres_widgets(self):
        self._login("diego_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertIsNotNone(response.context["presupuestos_pendientes"])
        self.assertIsNotNone(response.context["stock_bajo_total"])
        self.assertIsNotNone(response.context["mis_trabajos_activos"])

    def test_gabriel_ve_solo_stock_bajo(self):
        self._login("gabriel_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertNotIn("presupuestos_pendientes", response.context)
        self.assertIn("stock_bajo_total", response.context)
        self.assertNotIn("mis_trabajos_activos", response.context)

    def test_rodrigo_ve_presupuestos_y_trabajos_no_stock(self):
        self._login("rodrigo_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertIn("presupuestos_pendientes", response.context)
        self.assertNotIn("stock_bajo_total", response.context)
        self.assertIn("mis_trabajos_activos", response.context)

    def test_presupuestos_pendientes_cuenta_solo_enviados(self):
        enviado = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=enviado, descripcion_manual="X", precio_unitario=Decimal("100"), orden=0
        )
        enviar_presupuesto(enviado, self.diego)

        aceptado = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=aceptado, descripcion_manual="X", precio_unitario=Decimal("100"), orden=0
        )
        enviar_presupuesto(aceptado, self.diego)
        cambiar_estado(aceptado, EstadoPresupuesto.ACEPTADO, self.diego)

        self._login("diego_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["presupuestos_pendientes"], 1)

    def test_stock_bajo_refleja_productos_con_stock_bajo(self):
        marca = Marca.objects.create(nombre="Marca Dashboard")
        producto = Producto.objects.create(
            marca=marca, codigo="DASH1", nombre="Producto Dashboard",
            stock_minimo_general=Decimal("10"),
        )
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("2"), usuario=self.diego,
        )

        self._login("diego_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["stock_bajo_total"], 1)
        self.assertEqual(response.context["stock_bajo"][0][0], producto)

    def test_mis_trabajos_activos_es_por_usuario_y_excluye_terminados(self):
        def _trabajo(tecnico):
            presupuesto = Presupuesto.objects.create(cliente=self.cliente)
            ItemPresupuesto.objects.create(
                presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100"), orden=0
            )
            enviar_presupuesto(presupuesto, self.diego)
            cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
            return crear_trabajo(presupuesto, self.diego, tecnico_asignado=tecnico)

        activo_de_andres = _trabajo(self.andres)
        terminado_de_andres = _trabajo(self.andres)
        cambiar_estado_trabajo(
            terminado_de_andres,
            EstadoTrabajo.EN_EJECUCION,
            self.diego,
        )
        finalizar_trabajo(terminado_de_andres, self.diego)
        _trabajo(self.diego)  # de otro técnico, no debe contarse para Andrés

        self._login("andres_dashboard")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["mis_trabajos_activos"], 1)
