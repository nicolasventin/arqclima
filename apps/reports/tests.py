from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.clients.models import Cliente
from apps.jobs.models import EstadoTrabajo
from apps.jobs.services import (
    cambiar_estado_trabajo,
    crear_trabajo,
    enviar_materiales_pendientes,
    generar_listado_materiales,
    registrar_sobrante,
)
from apps.pricing.services import registrar_costo
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, TipoDescuento
from apps.quotes.services import cambiar_estado, enviar_presupuesto
from apps.stock.models import Deposito, TipoMovimiento
from apps.stock.services import registrar_movimiento

from .permissions import (
    puede_ver_montos_confidenciales,
    puede_ver_reporte_comercial,
    puede_ver_reporte_rentabilidad,
    puede_ver_reporte_stock,
)
from .services import (
    diferencia_enviado_utilizado,
    ganancia_presupuesto,
    ganancia_trabajo,
    material_mas_utilizado,
    metricas_comerciales,
    metricas_rentabilidad,
    metricas_stock,
    montos_comerciales,
    montos_rentabilidad,
    montos_stock,
    presupuestos_realizados_en,
    stock_valorizado,
    trabajos_terminados_en,
)


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class MetricasComercialesTests(TestCase):
    def setUp(self):
        self.rodrigo = _crear_usuario("rodrigo_reports_metricas", "Ventas y Presupuestos")
        self.cliente = Cliente.objects.create(nombre="Cliente Reports Métricas")
        self.hoy = timezone.localdate()

    def _presupuesto(self, precio=Decimal("1000"), descuento_tipo=None, descuento_valor=Decimal("0")):
        kwargs = {"cliente": self.cliente, "creado_por": self.rodrigo}
        if descuento_tipo is not None:
            kwargs["descuento_general_tipo"] = descuento_tipo
            kwargs["descuento_general_valor"] = descuento_valor
        presupuesto = Presupuesto.objects.create(**kwargs)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=precio
        )
        return presupuesto

    def test_presupuesto_nunca_enviado_no_cuenta(self):
        self._presupuesto()  # queda en Borrador
        realizados = presupuestos_realizados_en(self.hoy.year, self.hoy.month)
        self.assertEqual(realizados.count(), 0)

    def test_presupuesto_enviado_cuenta_como_realizado(self):
        presupuesto = self._presupuesto()
        enviar_presupuesto(presupuesto, self.rodrigo)
        realizados = presupuestos_realizados_en(self.hoy.year, self.hoy.month)
        self.assertIn(presupuesto, realizados)

    def test_no_cuenta_en_otro_mes(self):
        presupuesto = self._presupuesto()
        enviar_presupuesto(presupuesto, self.rodrigo)
        mes_pasado = self.hoy.month - 1 or 12
        anio_pasado = self.hoy.year if self.hoy.month != 1 else self.hoy.year - 1
        realizados = presupuestos_realizados_en(anio_pasado, mes_pasado)
        self.assertNotIn(presupuesto, realizados)

    def test_conteo_por_estado_y_tasa_conversion(self):
        aceptado = self._presupuesto()
        enviar_presupuesto(aceptado, self.rodrigo)
        cambiar_estado(aceptado, EstadoPresupuesto.ACEPTADO, self.rodrigo)

        rechazado = self._presupuesto()
        enviar_presupuesto(rechazado, self.rodrigo)
        cambiar_estado(rechazado, EstadoPresupuesto.RECHAZADO, self.rodrigo)

        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(metricas["total_realizados"], 2)
        self.assertEqual(metricas["aceptados"], 1)
        self.assertEqual(metricas["rechazados"], 1)
        self.assertEqual(metricas["tasa_conversion"], Decimal("50"))

    def test_tasa_conversion_none_sin_presupuestos(self):
        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertIsNone(metricas["tasa_conversion"])
        self.assertIsNone(metricas["descuento_promedio"])

    def test_descuento_promedio_con_porcentaje(self):
        presupuesto = self._presupuesto(descuento_tipo=TipoDescuento.PORCENTAJE, descuento_valor=Decimal("10"))
        enviar_presupuesto(presupuesto, self.rodrigo)
        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(metricas["descuento_promedio"], Decimal("10"))

    def test_descuento_promedio_con_monto_fijo_se_convierte_a_porcentaje_equivalente(self):
        # precio 1000, descuento fijo de 100 -> 10% equivalente
        presupuesto = self._presupuesto(
            precio=Decimal("1000"), descuento_tipo=TipoDescuento.MONTO, descuento_valor=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(metricas["descuento_promedio"], Decimal("10"))

    def test_reabierto_a_borrador_se_cuenta_aparte_pero_sigue_en_total(self):
        presupuesto = self._presupuesto()
        enviar_presupuesto(presupuesto, self.rodrigo)
        cambiar_estado(presupuesto, EstadoPresupuesto.BORRADOR, self.rodrigo)

        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(metricas["total_realizados"], 1)
        self.assertEqual(metricas["reabiertos_a_borrador"], 1)
        self.assertEqual(metricas["aceptados"], 0)

    def test_reenvio_no_duplica_el_conteo(self):
        presupuesto = self._presupuesto()
        enviar_presupuesto(presupuesto, self.rodrigo)
        cambiar_estado(presupuesto, EstadoPresupuesto.BORRADOR, self.rodrigo)
        enviar_presupuesto(presupuesto, self.rodrigo)

        metricas = metricas_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(metricas["total_realizados"], 1)


class MontosComercialesTests(TestCase):
    def setUp(self):
        self.rodrigo = _crear_usuario("rodrigo_reports_montos", "Ventas y Presupuestos")
        self.cliente = Cliente.objects.create(nombre="Cliente Reports Montos")
        self.hoy = timezone.localdate()

    def _presupuesto_enviado(self, precio):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente, creado_por=self.rodrigo)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=precio
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        return presupuesto

    def test_facturacion_potencial_solo_suma_enviados_sin_resolver(self):
        enviado = self._presupuesto_enviado(Decimal("1000"))
        aceptado = self._presupuesto_enviado(Decimal("500"))
        cambiar_estado(aceptado, EstadoPresupuesto.ACEPTADO, self.rodrigo)
        rechazado = self._presupuesto_enviado(Decimal("300"))
        cambiar_estado(rechazado, EstadoPresupuesto.RECHAZADO, self.rodrigo)

        montos = montos_comerciales(self.hoy.year, self.hoy.month)
        self.assertEqual(montos["facturacion_potencial"], Decimal("1000.00"))
        self.assertEqual(montos["facturacion_aceptada"], Decimal("500.00"))


class PermisosReportesTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_reports_permisos", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_reports_permisos", "Ventas y Presupuestos")
        self.gabriel = _crear_usuario("gabriel_reports_permisos", "Service y Repuestos")
        self.contri = _crear_usuario("contri_reports_permisos", "Depósito")

    def test_solo_diego_y_rodrigo_ven_el_reporte_comercial(self):
        self.assertTrue(puede_ver_reporte_comercial(self.diego))
        self.assertTrue(puede_ver_reporte_comercial(self.rodrigo))
        self.assertFalse(puede_ver_reporte_comercial(self.gabriel))
        self.assertFalse(puede_ver_reporte_comercial(self.contri))

    def test_solo_diego_ve_montos_confidenciales(self):
        self.assertTrue(puede_ver_montos_confidenciales(self.diego))
        self.assertFalse(puede_ver_montos_confidenciales(self.rodrigo))
        self.assertFalse(puede_ver_montos_confidenciales(self.gabriel))
        self.assertFalse(puede_ver_montos_confidenciales(self.contri))

    def test_solo_diego_ve_el_reporte_de_rentabilidad(self):
        self.assertTrue(puede_ver_reporte_rentabilidad(self.diego))
        self.assertFalse(puede_ver_reporte_rentabilidad(self.rodrigo))
        self.assertFalse(puede_ver_reporte_rentabilidad(self.gabriel))
        self.assertFalse(puede_ver_reporte_rentabilidad(self.contri))

    def test_diego_contri_y_gabriel_ven_el_reporte_de_stock(self):
        self.assertTrue(puede_ver_reporte_stock(self.diego))
        self.assertTrue(puede_ver_reporte_stock(self.gabriel))
        self.assertTrue(puede_ver_reporte_stock(self.contri))
        self.assertFalse(puede_ver_reporte_stock(self.rodrigo))


class ReporteComercialViewTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_reports_view", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_reports_view", "Ventas y Presupuestos")
        self.contri = _crear_usuario("contri_reports_view", "Depósito")

    def test_anonimo_no_puede_ver(self):
        response = self.client.get(reverse("reports:comercial"))
        self.assertEqual(response.status_code, 403)

    def test_contri_no_puede_ver(self):
        self.client.login(username="contri_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"))
        self.assertEqual(response.status_code, 403)

    def test_diego_ve_montos(self):
        self.client.login(username="diego_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("montos", response.context)
        self.assertContains(response, "Confidencial")

    def test_rodrigo_no_ve_montos(self):
        self.client.login(username="rodrigo_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("montos", response.context)
        self.assertNotContains(response, "Confidencial")

    def test_muestra_disclaimer_de_estado_actual(self):
        self.client.login(username="rodrigo_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"))
        self.assertContains(response, "situación")

    def test_navegacion_de_mes_diciembre_pasa_a_enero_del_anio_siguiente(self):
        self.client.login(username="diego_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"), {"anio": 2026, "mes": 12})
        self.assertEqual(response.context["anio_siguiente"], 2027)
        self.assertEqual(response.context["mes_siguiente"], 1)
        self.assertEqual(response.context["anio_anterior"], 2026)
        self.assertEqual(response.context["mes_anterior"], 11)

    def test_navegacion_de_mes_enero_pasa_a_diciembre_del_anio_anterior(self):
        self.client.login(username="diego_reports_view", password="clave12345")
        response = self.client.get(reverse("reports:comercial"), {"anio": 2026, "mes": 1})
        self.assertEqual(response.context["anio_anterior"], 2025)
        self.assertEqual(response.context["mes_anterior"], 12)


def _crear_producto_con_costo(marca, codigo, costo, usuario, es_repuesto=False):
    """Producto + un único ProductoProveedor con un costo cargado — atajo para tests de Rentabilidad/Stock."""
    producto = Producto.objects.create(
        marca=marca, codigo=codigo, nombre=f"Producto {codigo}", es_repuesto=es_repuesto
    )
    proveedor = Proveedor.objects.create(nombre_comercial=f"Proveedor {codigo}")
    pp = ProductoProveedor.objects.create(producto=producto, proveedor=proveedor)
    registrar_costo(pp, costo, usuario)
    return producto, pp


class GananciaPresupuestoTests(TestCase):
    def setUp(self):
        self.rodrigo = _crear_usuario("rodrigo_ganancia_presu", "Ventas y Presupuestos")
        self.cliente = Cliente.objects.create(nombre="Cliente Ganancia Presupuesto")
        self.marca = Marca.objects.create(nombre="Marca Ganancia Presupuesto")

    def test_ingreso_menos_costo_cotizado(self):
        producto, pp = _crear_producto_con_costo(self.marca, "GP1", Decimal("600"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            orden=0,
        )
        self.assertEqual(ganancia_presupuesto(presupuesto), Decimal("800.00"))

    def test_descuento_general_reduce_ingreso_pero_no_costo(self):
        producto, pp = _crear_producto_con_costo(self.marca, "GP2", Decimal("600"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente,
            descuento_general_tipo=TipoDescuento.PORCENTAJE,
            descuento_general_valor=Decimal("10"),
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            orden=0,
        )
        # ingreso: 2000 * 0.9 = 1800; costo sin cambios: 1200 -> ganancia 600 (no 800:
        # el descuento general SÍ le pega al ingreso, aunque no exista a nivel de ítem).
        self.assertEqual(ganancia_presupuesto(presupuesto), Decimal("600.00"))

    def test_escala_el_costo_por_cantidad_unidades(self):
        producto, pp = _crear_producto_con_costo(self.marca, "GP3", Decimal("600"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente, cantidad_unidades=3)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            orden=0,
        )
        # ingreso: 2*1000*3 = 6000; costo: 2*600*3 = 3600 -> ganancia 2400 (sin escalar el
        # costo daría 4800, inflando la ganancia — mismo tipo de error que el bug de
        # generar_listado_materiales corregido en cf380c6).
        self.assertEqual(ganancia_presupuesto(presupuesto), Decimal("2400.00"))

    def test_items_sin_costo_cargado_quedan_fuera_del_costo(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Mano de obra",
            cantidad=Decimal("1"), precio_unitario=Decimal("500"), orden=0,
        )
        self.assertEqual(ganancia_presupuesto(presupuesto), Decimal("500.00"))

    def test_items_opcionales_no_incluidos_quedan_fuera(self):
        producto, pp = _crear_producto_con_costo(self.marca, "GP4", Decimal("600"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            opcional=True, incluido=False, orden=0,
        )
        self.assertEqual(ganancia_presupuesto(presupuesto), Decimal("0.00"))


class GananciaTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_ganancia_trabajo", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Ganancia Trabajo")
        self.marca = Marca.objects.create(nombre="Marca Ganancia Trabajo")

    def _presupuesto_con_catalogo_y_mano_de_obra(self, cantidad_unidades=1):
        producto, pp = _crear_producto_con_costo(self.marca, f"GT-{cantidad_unidades}", Decimal("600"), self.diego)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente, cantidad_unidades=cantidad_unidades)
        item_catalogo = ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            orden=0,
        )
        item_mano_obra = ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Mano de obra",
            cantidad=Decimal("1"), precio_unitario=Decimal("500"), costo_unitario=Decimal("300"),
            orden=1,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)
        return trabajo, item_catalogo, item_mano_obra

    def test_material_enviado_completo_usa_cantidad_usada_neta_para_el_catalogo(self):
        trabajo, item_catalogo, item_mano_obra = self._presupuesto_con_catalogo_y_mano_de_obra()
        generar_listado_materiales(trabajo, self.diego)
        enviar_materiales_pendientes(trabajo, self.diego)

        resultado = ganancia_trabajo(trabajo)
        # ingreso: 2*1000 + 1*500 = 2500
        # costo catálogo (MaterialTrabajo, todo enviado): 600*2 = 1200
        # costo mano de obra (sin MaterialTrabajo): 300*1*1 = 300
        self.assertEqual(resultado["ingreso"], Decimal("2500.00"))
        self.assertEqual(resultado["costo"], Decimal("1500.00"))
        self.assertEqual(resultado["ganancia"], Decimal("1000.00"))
        self.assertFalse(resultado["tiene_costos_incompletos"])

    def test_sobrante_devuelto_reduce_el_costo_sin_tocar_el_ingreso(self):
        trabajo, item_catalogo, item_mano_obra = self._presupuesto_con_catalogo_y_mano_de_obra()
        generar_listado_materiales(trabajo, self.diego)
        enviar_materiales_pendientes(trabajo, self.diego)
        material = trabajo.materiales.get(producto=item_catalogo.producto)
        registrar_sobrante(material, Decimal("1"), self.diego)

        resultado = ganancia_trabajo(trabajo)
        # cantidad_usada_neta = 2 - 1 = 1 -> costo catálogo 600; mano de obra sigue en 300 -> 900
        self.assertEqual(resultado["costo"], Decimal("900.00"))
        self.assertEqual(resultado["ingreso"], Decimal("2500.00"))
        self.assertEqual(resultado["ganancia"], Decimal("1600.00"))

    def test_mano_de_obra_escala_por_cantidad_unidades(self):
        trabajo, item_catalogo, item_mano_obra = self._presupuesto_con_catalogo_y_mano_de_obra(
            cantidad_unidades=3
        )
        generar_listado_materiales(trabajo, self.diego)
        enviar_materiales_pendientes(trabajo, self.diego)

        resultado = ganancia_trabajo(trabajo)
        # ingreso: (2*1000 + 1*500) * 3 = 7500
        # costo catálogo: cantidad_necesaria ya escalada (2*3=6) enviada completa -> 600*6 = 3600
        # costo mano de obra: 300*1*3 = 900
        self.assertEqual(resultado["ingreso"], Decimal("7500.00"))
        self.assertEqual(resultado["costo"], Decimal("4500.00"))
        self.assertEqual(resultado["ganancia"], Decimal("3000.00"))

    def test_costo_unitario_none_marca_costos_incompletos_y_queda_fuera_del_costo(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Mano de obra sin costo",
            cantidad=Decimal("1"), precio_unitario=Decimal("500"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        resultado = ganancia_trabajo(trabajo)
        self.assertEqual(resultado["costo"], Decimal("0"))
        self.assertEqual(resultado["ingreso"], Decimal("500.00"))
        self.assertTrue(resultado["tiene_costos_incompletos"])

    def test_items_opcionales_no_incluidos_quedan_fuera(self):
        producto, pp = _crear_producto_con_costo(self.marca, "GT-OPC", Decimal("600"), self.diego)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"),
            opcional=True, incluido=False, orden=0,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        resultado = ganancia_trabajo(trabajo)
        self.assertEqual(resultado["ingreso"], Decimal("0.00"))
        self.assertEqual(resultado["costo"], Decimal("0"))
        self.assertFalse(resultado["tiene_costos_incompletos"])


class TrabajosTerminadosEnTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_trabajos_terminados", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Trabajos Terminados")
        self.hoy = timezone.localdate()

    def _trabajo(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        return crear_trabajo(presupuesto, self.diego)

    def test_incluye_solo_terminados_de_presupuestos_realizados_en_el_periodo(self):
        terminado = self._trabajo()
        cambiar_estado_trabajo(terminado, EstadoTrabajo.TERMINADO, self.diego)
        pendiente = self._trabajo()

        resultado = trabajos_terminados_en(self.hoy.year, self.hoy.month)
        self.assertIn(terminado, resultado)
        self.assertNotIn(pendiente, resultado)


class MetricasRentabilidadTests(TestCase):
    def setUp(self):
        self.rodrigo = _crear_usuario("rodrigo_metricas_rentabilidad", "Ventas y Presupuestos")
        self.cliente = Cliente.objects.create(nombre="Cliente Metricas Rentabilidad")
        self.marca = Marca.objects.create(nombre="Marca Metricas Rentabilidad")
        self.hoy = timezone.localdate()

    def test_margen_promedio_sobre_items_con_costo(self):
        producto, pp = _crear_producto_con_costo(self.marca, "MR1", Decimal("500"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("1"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("500"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.rodrigo)

        metricas = metricas_rentabilidad(self.hoy.year, self.hoy.month)
        # margen = (1000/500 - 1) * 100 = 100%
        self.assertEqual(metricas["margen_promedio"], Decimal("100"))

    def test_items_sin_costo_no_afectan_el_promedio_y_devuelve_none_si_no_hay_ninguno(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Manual sin costo",
            precio_unitario=Decimal("500"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.rodrigo)

        metricas = metricas_rentabilidad(self.hoy.year, self.hoy.month)
        self.assertIsNone(metricas["margen_promedio"])
        self.assertEqual(metricas["productos_mejor_margen"], [])

    def test_productos_mejor_margen_agrega_por_producto(self):
        producto, pp = _crear_producto_con_costo(self.marca, "MR2", Decimal("500"), self.rodrigo)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("1"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("500"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.rodrigo)

        metricas = metricas_rentabilidad(self.hoy.year, self.hoy.month)
        self.assertEqual(len(metricas["productos_mejor_margen"]), 1)
        self.assertEqual(metricas["productos_mejor_margen"][0]["producto"], producto)
        self.assertEqual(metricas["productos_mejor_margen"][0]["margen_promedio"], Decimal("100"))


class MontosRentabilidadTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_montos_rentabilidad", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Montos Rentabilidad")
        self.marca = Marca.objects.create(nombre="Marca Montos Rentabilidad")
        self.hoy = timezone.localdate()

    def test_totales_suman_las_filas_de_presupuestos_y_trabajos(self):
        producto, pp = _crear_producto_con_costo(self.marca, "MO1", Decimal("600"), self.diego)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("1000"), costo_unitario=Decimal("600"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)
        generar_listado_materiales(trabajo, self.diego)
        enviar_materiales_pendientes(trabajo, self.diego)
        cambiar_estado_trabajo(trabajo, EstadoTrabajo.TERMINADO, self.diego)

        montos = montos_rentabilidad(self.hoy.year, self.hoy.month)
        self.assertEqual(len(montos["ganancia_presupuestos"]), 1)
        self.assertEqual(montos["ganancia_total_presupuestos"], Decimal("800.00"))
        self.assertEqual(len(montos["ganancia_trabajos"]), 1)
        self.assertEqual(montos["ganancia_total_trabajos"], Decimal("800.00"))


class StockValorizadoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_stock_valorizado", "Administrador")
        self.marca = Marca.objects.create(nombre="Marca Stock Valorizado")

    def test_usa_el_costo_mas_reciente_entre_proveedores(self):
        producto = Producto.objects.create(marca=self.marca, codigo="SV1", nombre="Producto SV1")
        pp_a = ProductoProveedor.objects.create(
            producto=producto, proveedor=Proveedor.objects.create(nombre_comercial="Proveedor SV A")
        )
        pp_b = ProductoProveedor.objects.create(
            producto=producto, proveedor=Proveedor.objects.create(nombre_comercial="Proveedor SV B")
        )
        registrar_costo(pp_a, Decimal("100"), self.diego)
        registrar_costo(pp_b, Decimal("150"), self.diego)  # más reciente
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"), usuario=self.diego,
        )

        resultado = stock_valorizado()
        fila = next(f for f in resultado["detalle"] if f["producto"] == producto)
        self.assertEqual(fila["costo_unitario"], Decimal("150.00"))
        self.assertEqual(fila["valor"], Decimal("1500.00"))
        self.assertEqual(resultado["total"], Decimal("1500.00"))

    def test_producto_sin_costo_cargado_queda_fuera(self):
        producto = Producto.objects.create(marca=self.marca, codigo="SV2", nombre="Producto SV2")
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"), usuario=self.diego,
        )
        resultado = stock_valorizado()
        self.assertFalse(any(f["producto"] == producto for f in resultado["detalle"]))

    def test_producto_sin_stock_neto_queda_fuera(self):
        producto, pp = _crear_producto_con_costo(self.marca, "SV3", Decimal("100"), self.diego)
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"), usuario=self.diego,
        )
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego,
        )
        resultado = stock_valorizado()
        self.assertFalse(any(f["producto"] == producto for f in resultado["detalle"]))

    def test_deposito_repuestos_solo_se_evalua_si_es_repuesto(self):
        producto = Producto.objects.create(
            marca=self.marca, codigo="SV4", nombre="Producto SV4", es_repuesto=False
        )
        pp = ProductoProveedor.objects.create(
            producto=producto, proveedor=Proveedor.objects.create(nombre_comercial="Proveedor SV4")
        )
        registrar_costo(pp, Decimal("100"), self.diego)
        registrar_movimiento(
            producto=producto, deposito=Deposito.REPUESTOS, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"), usuario=self.diego,
        )
        resultado = stock_valorizado()
        self.assertFalse(any(f["producto"] == producto for f in resultado["detalle"]))


class MaterialMasUtilizadoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_material_mas_utilizado", "Administrador")
        self.marca = Marca.objects.create(nombre="Marca Material Mas Utilizado")
        self.hoy = timezone.localdate()

    def test_ordena_por_cantidad_de_salida_descendente(self):
        producto_a = Producto.objects.create(marca=self.marca, codigo="MU1", nombre="Producto MU1")
        producto_b = Producto.objects.create(marca=self.marca, codigo="MU2", nombre="Producto MU2")
        registrar_movimiento(
            producto=producto_a, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-5"), usuario=self.diego,
        )
        registrar_movimiento(
            producto=producto_b, deposito=Deposito.GENERAL, tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-8"), usuario=self.diego,
        )

        resultado = material_mas_utilizado(self.hoy.year, self.hoy.month)
        self.assertEqual(resultado[0]["producto_id"], producto_b.pk)
        self.assertEqual(resultado[0]["cantidad"], Decimal("8"))
        self.assertEqual(resultado[1]["producto_id"], producto_a.pk)

    def test_entradas_no_cuentan_como_utilizacion(self):
        producto = Producto.objects.create(marca=self.marca, codigo="MU3", nombre="Producto MU3")
        registrar_movimiento(
            producto=producto, deposito=Deposito.GENERAL, tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"), usuario=self.diego,
        )
        resultado = material_mas_utilizado(self.hoy.year, self.hoy.month)
        self.assertFalse(any(f["producto_id"] == producto.pk for f in resultado))


class DiferenciaEnviadoUtilizadoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_diferencia_env_us", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Diferencia")
        self.marca = Marca.objects.create(nombre="Marca Diferencia")
        self.hoy = timezone.localdate()

    def _trabajo_con_material(self):
        producto, pp = _crear_producto_con_costo(self.marca, "DU1", Decimal("100"), self.diego)
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, producto_proveedor=pp,
            cantidad=Decimal("2"), precio_unitario=Decimal("500"), costo_unitario=Decimal("100"), orden=0,
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)
        generar_listado_materiales(trabajo, self.diego)
        enviar_materiales_pendientes(trabajo, self.diego)
        return trabajo

    def test_incluye_material_con_sobrante_devuelto(self):
        trabajo = self._trabajo_con_material()
        material = trabajo.materiales.get()
        registrar_sobrante(material, Decimal("1"), self.diego)

        resultado = diferencia_enviado_utilizado(self.hoy.year, self.hoy.month)
        fila = next(f for f in resultado if f["material"] == material)
        self.assertEqual(fila["enviado"], Decimal("2"))
        self.assertEqual(fila["usado"], Decimal("1"))
        self.assertEqual(fila["diferencia"], Decimal("1"))

    def test_material_totalmente_consumido_no_aparece(self):
        trabajo = self._trabajo_con_material()
        material = trabajo.materiales.get()

        resultado = diferencia_enviado_utilizado(self.hoy.year, self.hoy.month)
        self.assertFalse(any(f["material"] == material for f in resultado))


class ReporteRentabilidadViewTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_rentabilidad_view", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_rentabilidad_view", "Ventas y Presupuestos")

    def test_solo_diego_puede_ver(self):
        response = self.client.get(reverse("reports:rentabilidad"))
        self.assertEqual(response.status_code, 403)

        self.client.login(username="rodrigo_rentabilidad_view", password="clave12345")
        response = self.client.get(reverse("reports:rentabilidad"))
        self.assertEqual(response.status_code, 403)

        self.client.login(username="diego_rentabilidad_view", password="clave12345")
        response = self.client.get(reverse("reports:rentabilidad"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("montos", response.context)


class ReporteStockViewTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_stock_view", "Administrador")
        self.contri = _crear_usuario("contri_stock_view", "Depósito")
        self.gabriel = _crear_usuario("gabriel_stock_view", "Service y Repuestos")
        self.rodrigo = _crear_usuario("rodrigo_stock_view", "Ventas y Presupuestos")

    def test_rodrigo_no_puede_ver(self):
        self.client.login(username="rodrigo_stock_view", password="clave12345")
        response = self.client.get(reverse("reports:stock"))
        self.assertEqual(response.status_code, 403)

    def test_diego_ve_el_reporte_con_montos(self):
        self.client.login(username="diego_stock_view", password="clave12345")
        response = self.client.get(reverse("reports:stock"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("montos", response.context)

    def test_contri_y_gabriel_ven_el_reporte_sin_montos(self):
        for username in ("contri_stock_view", "gabriel_stock_view"):
            self.client.login(username=username, password="clave12345")
            response = self.client.get(reverse("reports:stock"))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("montos", response.context)
