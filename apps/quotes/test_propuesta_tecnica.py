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


    def _presupuesto_largo_pdf(self):
        presupuesto = self._presupuesto_muestra()
        presupuesto.alcance_tecnico = "\n".join(
            [
                "Se ha contemplado la provisión e instalación de un sistema de piso radiante para el proyecto según plano adjunto.",
                "La Caldera se ha previsto ubicarla sobre muro de gabinete exterior junto a churrasquera, según plano.",
                "El termostato será ubicado sobre pared interna lejos de ventanas.",
                "El colector será ubicado sobre muro de ingreso a Dormitorio Norte.",
                "Balance térmico: se contempla sistema de construcción tradicional y aberturas simples.",
                "Trabajos a realizar: se propone ejecutar el proyecto en dos etapas.",
                "Tiempo de ejecución 1era etapa: 3 a 4 días, máximo.",
                "Tiempo de ejecución 2da etapa: se resuelve en el día.",
                "La temperatura exterior considerada: -4° C.",
                "La temperatura interior de confort: 21° C.",
            ]
        )
        presupuesto.notas_cliente = "\n".join(
            [
                "Mano de obra 1° y 2° etapa sujeta a cambios por inflación al momento de ejecución.",
                "En el caso que ventilación estándar de caldera no sea suficiente, contemplar accesorios adicionales.",
                "Montos son por unidad habitacional. Sumar según corresponda.",
                "Cantidad y ubicación de colector es tentativo, a consensuar con propietarios o arquitecto/a.",
                "Se deja presupuesto abierto a posibles modificaciones por cambios en listas de precios.",
            ]
        )
        presupuesto.forma_pago = "\n".join(
            [
                "Contado o transferencia: 8% de descuento sobre materiales y equipamiento de ambas etapas.",
                "Echeqs 0-30-60 sin recargo.",
                "Consultar financiación.",
            ]
        )
        presupuesto.garantia = (
            "Se garantizan los equipos antes mencionados, los materiales utilizados y "
            "la instalación a realizar por el término de un año, para lo cual deberán "
            "utilizarse de acuerdo al manual de uso y mantenimiento."
        )
        presupuesto.exclusiones = "\n".join(
            [
                "Electricidad al pie de los equipos según se indique.",
                "Cañería y tendido de cables entre termostatos y equipo.",
                "Alimentación de agua al pie del equipo según se indique.",
                "Colocación de Gabinetes losa radiante en caso que vayan embutidos.",
                "Canaletas en piso en caso que esté el contrapiso terminado.",
                "Trabajos de terminaciones, impermeabilizaciones y albañilería.",
                "Contrapiso nivelado para recibir sándwich de aislación y malla sujeción caño PEX.",
                "Roturas de cañería se cobrarán como adicionales de obra.",
            ]
        )
        presupuesto.save(
            update_fields=[
                "alcance_tecnico",
                "notas_cliente",
                "forma_pago",
                "garantia",
                "exclusiones",
            ]
        )

        etapa1 = presupuesto.secciones.get(titulo="1ERA ETAPA CALEFACCIÓN")
        etapa1.descripcion_publica = "\n".join(
            [
                "1 colector de bronce de 6 circuitos con válvulas de corte, grifo y purgador automático.",
                "Cañería PEX 20 mm marca SALADILLO, total 550 mts.",
                "Cañería de interconexión de colector hasta caldera en IPS MAXUM.",
                "Aislación de piso con manta térmica SALADILLO.",
                "NO SE COTIZA MALLA SIMA.",
            ]
        )
        etapa1.save(update_fields=["descripcion_publica"])

        etapa2 = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="2DA ETAPA CALEFACCIÓN",
            orden=1,
            descripcion_publica="\n".join(
                [
                    "1 Caldera mural CALDAIA modelo ECCO 24 DS TF.",
                    "1 Kit de conexiones hidráulicas.",
                    "Salida de humos con codo coaxial y tramo de 1 ml.",
                    "1 Termostato común ASUA.",
                ]
            ),
        )
        LineaComercialPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=etapa2,
            etiqueta="Equipamiento",
            monto=Decimal("2298500"),
            tipo_iva=TipoIVA.INCLUIDO,
            orden=3,
        )
        LineaComercialPresupuesto.objects.create(
            presupuesto=presupuesto,
            seccion=etapa2,
            etiqueta="Mano de Obra",
            monto=Decimal("385000"),
            tipo_iva=TipoIVA.MAS_IVA,
            orden=4,
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


    def test_pdf_largo_es_compacto_y_renderiza_logo(self):
        presupuesto = self._presupuesto_largo_pdf()
        self.client.login(username=self.usuario.username, password="clave12345")

        response = self.client.get(reverse("quotes:pdf", args=[presupuesto.pk]))

        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.content))
        self.assertEqual(len(reader.pages), 2)

        def contiene_imagen(resources, visitados=None):
            if not resources:
                return False
            visitados = visitados or set()
            xobjects = resources.get("/XObject")
            if not xobjects:
                return False
            for referencia in xobjects.values():
                objeto = referencia.get_object()
                identidad = getattr(objeto, "indirect_reference", None)
                clave = repr(identidad) if identidad is not None else id(objeto)
                if clave in visitados:
                    continue
                visitados.add(clave)
                if objeto.get("/Subtype") == "/Image":
                    return True
                if objeto.get("/Subtype") == "/Form":
                    if contiene_imagen(objeto.get("/Resources"), visitados):
                        return True
            return False

        self.assertTrue(
            contiene_imagen(reader.pages[0].get("/Resources")),
            "El encabezado del PDF debe incluir el logo raster de ARQCLIMA.",
        )

        texto = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("Echeqs 0-30-60 sin recargo", texto)
        self.assertIn("Consultar financiación", texto)
        self.assertIn("GARANT", texto)
        self.assertIn("EXCLUSIONES", texto)

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
