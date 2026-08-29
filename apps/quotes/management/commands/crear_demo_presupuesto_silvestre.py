from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from apps.clients.models import Cliente
from apps.quotes.models import (
    LineaComercialPresupuesto,
    Presupuesto,
    SeccionPresupuesto,
    TipoIVA,
)


class Command(BaseCommand):
    help = (
        "Crea en desarrollo un presupuesto demo basado en el presupuesto real "
        "Silvestre / B° La Quebrada para revisar el PDF generado por ARQCLIMA."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Este comando demo solo puede ejecutarse con DEBUG=True.")

        User = get_user_model()
        usuario = User.objects.filter(is_superuser=True).order_by("pk").first()
        if usuario is None:
            usuario = User.objects.filter(is_staff=True).order_by("pk").first()

        cliente, _ = Cliente.objects.get_or_create(
            nombre="Marina Silvestre",
            defaults={"tipo": "particular", "activo": True},
        )

        presupuesto = Presupuesto.objects.create(
            cliente=cliente,
            obra="Proyecto 3 casas",
            direccion="Luján de Cuyo, Mendoza - B° La Quebrada",
            referencia=(
                "Provisión e instalación de sistema de calefacción por piso "
                "radiante para toda la vivienda, con un termostato de ambiente."
            ),
            titulo_propuesta="PISO RADIANTE",
            alcance_tecnico="\n".join(
                [
                    "Se ha contemplado la provisión e instalación de un sistema de piso radiante para el proyecto según plano adjunto. No se incluyen futuras ampliaciones que no estén indicadas en plano.",
                    "La Caldera se ha previsto ubicarla sobre muro de gabinete exterior junto a churrasquera, según plano. Se plantea salida de gases tipo estándar (no vertical).",
                    "El termostato será ubicado sobre pared interna lejos de ventanas.",
                    "El colector será ubicado sobre muro de ingreso a Dormitorio Norte (oculto en mueble).",
                    "Balance térmico: se contempla sistema de construcción tradicional y aberturas simples.",
                    "Trabajos a realizar: se propone ejecutar el proyecto en dos etapas. La primera, con el desarrollo de cañerías fusión (con prueba hidráulica), PEX/PERT, colectores y aislación de piso. La segunda, con la colocación de caldera, termostato y la puesta en marcha final del sistema.",
                    "Tiempo de ejecución 1era etapa: 3 a 4 días, máximo.",
                    "Tiempo de ejecución 2da etapa: se resuelve en el día.",
                    "La temperatura exterior considerada: -4° C.",
                    "La temperatura interior de confort: 21° C.",
                ]
            ),
            notas_cliente="\n".join(
                [
                    "Mano de obra 1° y 2° etapa sujeta a cambios por inflación al momento de ejecución. Montos sujetos a modificaciones por variación del índice nacional de precios al consumidor IPC.",
                    "En el caso que ventilación estándar de caldera no sea suficiente, contemplar accesorios adicionales (salida vertical).",
                    "Montos son por unidad habitacional. Sumar según corresponda.",
                    "Cantidad y ubicación de colector es tentativo, a consensuar con propietarios o arquitecto/a.",
                    "Se deja presupuesto abierto a posibles modificaciones por cambios en listas de precios o durante la ejecución.",
                ]
            ),
            forma_pago="\n".join(
                [
                    "Contado o transferencia: 8% de descuento sobre materiales y equipamiento de ambas etapas.",
                    "Echeqs 0-30-60 sin recargo.",
                    "Consultar financiación.",
                ]
            ),
            garantia=(
                "Se garantizan los equipos antes mencionados, los materiales utilizados "
                "y la instalación a realizar por el término de un año, para lo cual "
                "deberán utilizarse equipos y hacer uso de la instalación de acuerdo al "
                "manual de uso y manual de mantenimiento, en las condiciones originales "
                "de proyecto."
            ),
            exclusiones="\n".join(
                [
                    "Electricidad al pie de los equipos según se indique.",
                    "Cañería y tendido de cables entre termostatos y equipo.",
                    "Alimentación de agua al pie del equipo según se indique. Instalación debe asegurar al menos 1 kg de presión.",
                    "Colocación de Gabinetes losa radiante en caso que vayan embutidos. En caso de solicitar instalación de nuestra parte, tendrá un costo de $95.000 + IVA (por gabinete).",
                    "Canaletas en piso en caso que esté el contrapiso terminado.",
                    "Trabajos de terminaciones, impermeabilizaciones y albañilería.",
                    "Contrapiso nivelado para recibir sándwich de aislación y malla sujeción caño PEX.",
                    "Roturas de cañería se cobrarán como adicionales de obra, costo $160.000 + Repuestos.",
                ]
            ),
            firma_texto="Arq. Diego Ventin Ponte",
            cantidad_unidades=3,
            importes_por_unidad=True,
            mostrar_total_general=False,
            creado_por=usuario,
        )

        etapa1 = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="1ERA ETAPA CALEFACCIÓN",
            orden=0,
            descripcion_publica="\n".join(
                [
                    "1 (uno) colector de bronce de 6 circuitos, con válvulas de corte, grifo, purgador automático, y su respectivo gabinete metálico.",
                    "Cañería PEX 20 mm marca SALADILLO, de no más de 100 mts de longitud para cada circuito, con un total de 550 mts.",
                    "Cañería de interconexión de colector hasta caldera en IPS MAXUM. Con prueba de presión a 20 kg/cm² por 30 min y 3 kg/cm² hasta final de obra.",
                    "Aislación de piso con manta térmica SALADILLO (mismo coeficiente de aislación que telgopor de 10 mm de espesor).",
                    "NO SE COTIZA MALLA SIMA (12 unid. 15x15x3.9 de 2 x 5 mts).",
                ]
            ),
        )
        LineaComercialPresupuesto.objects.bulk_create(
            [
                LineaComercialPresupuesto(
                    presupuesto=presupuesto,
                    seccion=etapa1,
                    etiqueta="Materiales",
                    monto=Decimal("2400000"),
                    tipo_iva=TipoIVA.INCLUIDO,
                    orden=0,
                ),
                LineaComercialPresupuesto(
                    presupuesto=presupuesto,
                    seccion=etapa1,
                    etiqueta="Mano de Obra",
                    monto=Decimal("1022000"),
                    tipo_iva=TipoIVA.MAS_IVA,
                    orden=1,
                ),
                LineaComercialPresupuesto(
                    presupuesto=presupuesto,
                    seccion=etapa1,
                    etiqueta="Diferencia por aislación de piso con Telgopor de Alta Densidad",
                    descripcion="20 mm de espesor + Nylon de 200 micrones",
                    monto=Decimal("383000"),
                    tipo_iva=TipoIVA.INCLUIDO,
                    opcional=True,
                    incluido=False,
                    recomendado=True,
                    orden=2,
                ),
            ]
        )

        etapa2 = SeccionPresupuesto.objects.create(
            presupuesto=presupuesto,
            titulo="2DA ETAPA CALEFACCIÓN",
            orden=1,
            descripcion_publica="\n".join(
                [
                    "1 (Una) Caldera mural marca CALDAIA modelo ECCO 24 DS TF (26.000 kcal). De ensamble nacional con bomba y vaso de expansión incorporado.",
                    "1 (uno) Kit de conexiones hidráulicas.",
                    "Salida de humos: 1 codo coaxial 90° + 1 tramo de 1 ml coaxial con terminal anti viento bonificados con caldera.",
                    "1 (uno) Termostato común ASUA.",
                ]
            ),
        )
        LineaComercialPresupuesto.objects.bulk_create(
            [
                LineaComercialPresupuesto(
                    presupuesto=presupuesto,
                    seccion=etapa2,
                    etiqueta="Equipamiento",
                    monto=Decimal("2298500"),
                    tipo_iva=TipoIVA.INCLUIDO,
                    orden=3,
                ),
                LineaComercialPresupuesto(
                    presupuesto=presupuesto,
                    seccion=etapa2,
                    etiqueta="Mano de Obra",
                    monto=Decimal("385000"),
                    tipo_iva=TipoIVA.MAS_IVA,
                    orden=4,
                ),
            ]
        )

        self.stdout.write(self.style.SUCCESS(
            f"Demo creado: Presupuesto #{presupuesto.numero}"
        ))
        self.stdout.write(
            f"Abrí http://127.0.0.1:8000/presupuestos/{presupuesto.pk}/"
        )
        self.stdout.write(
            f"PDF real de la web: http://127.0.0.1:8000/presupuestos/{presupuesto.pk}/pdf/"
        )
