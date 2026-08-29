from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.models import User

from .views import _barras_relativas, _distribucion_comercial, _porcentaje_css


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class HelpersVisualesReportesTests(SimpleTestCase):
    def test_porcentaje_css_normaliza_con_punto_decimal(self):
        self.assertEqual(_porcentaje_css(Decimal("2.5"), Decimal("10")), "25.0%")
        self.assertEqual(_porcentaje_css(5, 0), "0%")

    def test_barras_relativas_normalizan_y_marcan_negativos(self):
        filas = _barras_relativas(
            [
                {"label": "A", "valor": Decimal("10")},
                {"label": "B", "valor": Decimal("-5")},
                {"label": "C", "valor": Decimal("0")},
            ],
            "valor",
        )

        self.assertEqual(filas[0]["chart_width"], "100.0%")
        self.assertEqual(filas[1]["chart_width"], "50.0%")
        self.assertTrue(filas[1]["chart_negative"])
        self.assertEqual(filas[2]["chart_width"], "0.0%")

    def test_distribucion_comercial_usa_total_como_base(self):
        metricas = {
            "total_realizados": 4,
            "aceptados": 2,
            "enviados_sin_resolver": 1,
            "rechazados": 1,
            "vencidos": 0,
            "cancelados": 0,
            "reabiertos_a_borrador": 0,
        }

        filas = _distribucion_comercial(metricas)

        aceptados = next(f for f in filas if f["label"] == "Aceptados")
        enviados = next(f for f in filas if f["label"] == "Enviados sin resolver")
        self.assertEqual(aceptados["chart_width"], "50.0%")
        self.assertEqual(enviados["chart_width"], "25.0%")


class ReportesVisualesViewTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_reportes_visuales", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_reportes_visuales", "Ventas y Presupuestos")

    def test_comercial_renderiza_kpis_y_grafico_de_estados(self):
        self.client.login(username=self.diego.username, password="clave12345")

        response = self.client.get(reverse("reports:comercial"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "report-kpi-grid")
        self.assertContains(response, "Estado actual de los presupuestos")
        self.assertContains(response, "report-bar-chart")
        self.assertIn("chart_estados", response.context)
        self.assertIn("periodo_fecha", response.context)

    def test_rentabilidad_prepara_graficos_sin_datos(self):
        self.client.login(username=self.diego.username, password="clave12345")

        response = self.client.get(reverse("reports:rentabilidad"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("chart_productos_margen", response.context)
        self.assertEqual(response.context["chart_productos_margen"], [])
        self.assertContains(response, "Productos con mejor margen")

    def test_stock_prepara_resumen_y_grafico_de_consumo(self):
        self.client.login(username=self.diego.username, password="clave12345")

        response = self.client.get(reverse("reports:stock"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("resumen_stock", response.context)
        self.assertIn("chart_material_uso", response.context)
        self.assertContains(response, "Material más utilizado")

    def test_clientes_prepara_dos_graficos_y_resumen(self):
        self.client.login(username=self.diego.username, password="clave12345")

        response = self.client.get(reverse("reports:clientes"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("chart_clientes_trabajos", response.context)
        self.assertIn("chart_clientes_pendientes", response.context)
        self.assertIn("resumen_clientes", response.context)
        self.assertContains(response, "Clientes con más trabajos")

    def test_empleados_prepara_resumen_visual(self):
        self.client.login(username=self.diego.username, password="clave12345")

        response = self.client.get(reverse("reports:empleados"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("resumen_empleados", response.context)
        self.assertContains(response, "Actividad del período")

    def test_rodrigo_no_recibe_montos_confidenciales_en_comercial(self):
        self.client.login(username=self.rodrigo.username, password="clave12345")

        response = self.client.get(reverse("reports:comercial"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("montos", response.context)
        self.assertNotIn("chart_montos", response.context)
        self.assertNotContains(response, "Facturación aceptada")
