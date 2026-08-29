from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from apps.accounts.models import User
from apps.clients.models import Cliente
from apps.pricing.models import ConfiguracionGeneral

from .models import (
    LineaComercialPresupuesto,
    Presupuesto,
    SeccionPresupuesto,
    TipoIVA,
)
from .services import calcular_totales


class PropuestaTecnicaPresupuestoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        grupo, _ = Group.objects.get_or_create(name="Ventas propuesta técnica")
        for codename in (
            "view_presupuesto",
            "add_presupuesto",
            "change_presupuesto",
            "add_itempresupuesto",
            "delete_itempresupuesto",
            "add_seccionpresupuesto",
            "delete_seccionpresupuesto",
        ):
            grupo.permissions.add(
                Permission.objects.get(
                    codename=codename,
                    content_type__app_label="quotes",
                )
            )

        cls.usuario = User.objects.create_user(
            username="ventas_propuesta",
            password="clave12345",
            first_name="Diego",
            last_name="Ventin Ponte",
        )
        cls.usuario.groups.add(grupo)
        cls.cliente = Cliente.objects.create(nombre="Marina Silvestre")

    def setUp(self):
        config = ConfiguracionGeneral.obtener()
        config.iva_pct = Decimal("21")
        config.save(update_fields=["iva_pct"])

    def _presupuesto_muestra(self):
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente,
            obra="Proyecto 3 casas",
            direccion="B° La Quebrada - Luján de Cuyo, Mendoza",
            referencia=(
                "Provisión e instalación de sistema de calefacción por piso "
                "radiante para toda la vivienda, con un termostato de ambiente."
            ),
            titulo_propuesta="PISO RADIANTE",
            alcance_tecnico=(
                "Se ha contemplado la provisión e instalación de un sistema de piso radiante.\n"
                "La Caldera se ha previsto ubicarla sobre muro de gabinete exterior.\n"
                "La temperatura interior de confort considerada es 21° C."
            ),
            notas_cliente="Montos son por unidad habitacional. Sumar según corresponda.",
            forma_pago=(
                "Contado o transferencia: 8% de descuento sobre materiales y equipamiento.\n"
                "Echeqs 0-30-60 sin recargo."
            ),
            garantia="Se garantizan equipos, materiales e instalación por el término de un año.",
            exclusiones="Electricidad al pie de los equipos.\nTrabajos de albañilería y terminaciones.",
            firma_texto="Arq. Diego Ventin Ponte",
            cantidad_unidades=3,
            importes_por_unidad=True,
            mostrar_total_general=False,
            creado_por=self.usuario,
        )
        etapa = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="1ERA ETAPA CALEFACCIÓN",
            descripcion_publica=(
                "1 colector de bronce de 6 circuitos.\n"
                "Cañería PEX 20 mm marca SALADILLO, total 550 mts.\n"
                "Aislación de piso con manta térmica."
            ),
        )
        LineaComercialPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=etapa,
            etiqueta="Materiales",
            monto=Decimal("2400000"),
            tipo_iva=TipoIVA.INCLUIDO,
        )
        LineaComercialPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=etapa,
            etiqueta="Mano de Obra",
            monto=Decimal("1022000"),
            tipo_iva=TipoIVA.MAS_IVA,
            orden=1,
        )
        LineaComercialPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=etapa,
            etiqueta="Diferencia por aislación con Telgopor de Alta Densidad",
            monto=Decimal("383000"),
            tipo_iva=TipoIVA.INCLUIDO,
            opcional=True,
            incluido=False,
            recomendado=True,
            orden=2,
        )
        return presupuesto

    def test_totales_comerciales_reemplazan_a_items_para_total_cliente(self):
        presupuesto = self._presupuesto_muestra()

        totales = calcular_totales(presupuesto)

        self.assertTrue(totales["usa_lineas_comerciales"])
        self.assertEqual(totales["subtotal_general"], Decimal("3636620.00"))
        self.assertEqual(totales["total_por_unidad"], Decimal("3636620.00"))
        self.assertEqual(totales["total_final"], Decimal("10909860.00"))

    def test_pdf_usa_formato_propuesta_y_oculta_total_si_se_configura(self):
        presupuesto = self._presupuesto_muestra()
        self.client.login(username=self.usuario.username, password="clave12345")

        response = self.client.get(reverse("quotes:pdf", args=[presupuesto.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

        reader = PdfReader(BytesIO(response.content))
        texto = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("PRESUPUESTO", texto)
        self.assertIn("PISO RADIANTE", texto)
        self.assertIn("1ERA ETAPA", texto)
        self.assertIn("FORMA DE PAGO", texto)
        self.assertIn("GARANT", texto)
        self.assertIn("EXCLUSIONES", texto)
        self.assertIn("Materiales", texto)
        self.assertIn("Mano de Obra", texto)
        self.assertNotIn("10909860", texto.replace(".", "").replace(",", ""))

    def test_detalle_separa_importes_cliente_de_items_internos(self):
        presupuesto = self._presupuesto_muestra()
        self.client.login(username=self.usuario.username, password="clave12345")

        response = self.client.get(reverse("quotes:detalle", args=[presupuesto.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importes que verá el cliente")
        self.assertContains(response, "Detalle interno de cálculo")
        self.assertContains(response, "Editar propuesta")
        self.assertContains(response, "Materiales")
        self.assertContains(response, "Opcional")

    def test_puede_agregar_importe_comercial_a_una_etapa(self):
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente,
            creado_por=self.usuario,
        )
        etapa = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="2DA ETAPA",
        )
        self.client.login(username=self.usuario.username, password="clave12345")

        response = self.client.post(
            reverse("quotes:agregar_linea_comercial", args=[presupuesto.pk]),
            {
                "seccion": etapa.pk,
                "etiqueta": "Equipamiento",
                "descripcion": "",
                "monto": "2298500",
                "tipo_iva": TipoIVA.INCLUIDO,
                "incluido": "on",
            },
        )

        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        linea = presupuesto.lineas_comerciales.get()
        self.assertEqual(linea.seccion, etapa)
        self.assertEqual(linea.etiqueta, "Equipamiento")
        self.assertEqual(linea.monto, Decimal("2298500"))

    def test_editar_propuesta_solo_en_borrador(self):
        presupuesto = self._presupuesto_muestra()
        self.client.login(username=self.usuario.username, password="clave12345")

        response = self.client.get(reverse("quotes:editar", args=[presupuesto.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar propuesta")
        self.assertContains(response, "Referencia")
        self.assertContains(response, "Forma de pago")
        self.assertContains(response, "Exclusiones")
