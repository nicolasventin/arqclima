from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.clients.models import Cliente
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, TipoDescuento
from apps.quotes.services import cambiar_estado, enviar_presupuesto

from .permissions import puede_ver_montos_confidenciales, puede_ver_reporte_comercial
from .services import metricas_comerciales, montos_comerciales, presupuestos_realizados_en


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
