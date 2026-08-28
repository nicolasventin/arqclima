from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pricing.models import ConfiguracionGeneral
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.tasks.models import Tarea, TipoAutomatizacion
from apps.tasks.services import crear_tarea_automatica


class Command(BaseCommand):
    """
    Regla de negocio 17: avisa al vendedor (Presupuesto.creado_por,
    no hay un campo "vendedor" separado) cuando un presupuesto Enviado
    está a N días o menos de vencer (ConfiguracionGeneral.
    dias_aviso_presupuesto_por_vencer).

    Idempotente: una sola tarea por presupuesto en toda su vida (a
    diferencia del seguimiento, acá no hace falta "reabrir la ventana"
    — fecha_vencimiento no cambia solo, y una vez avisado no hace
    falta insistir todos los días). Pensado para cron diario, sin
    scheduler propio en la infraestructura todavía.
    """

    help = "Avisa al vendedor de presupuestos Enviados que están por vencer."

    def handle(self, *args, **options):
        dias = ConfiguracionGeneral.obtener().dias_aviso_presupuesto_por_vencer
        hoy = timezone.localdate()
        limite = hoy + timezone.timedelta(days=dias)

        candidatos = Presupuesto.objects.filter(
            estado=EstadoPresupuesto.ENVIADO,
            fecha_vencimiento__isnull=False,
            fecha_vencimiento__gte=hoy,
            fecha_vencimiento__lte=limite,
        )

        total = 0
        for presupuesto in candidatos:
            ya_existe = Tarea.objects.filter(
                generada_por=TipoAutomatizacion.PRESUPUESTO_POR_VENCER,
                presupuesto=presupuesto,
            ).exists()
            if ya_existe:
                continue

            crear_tarea_automatica(
                TipoAutomatizacion.PRESUPUESTO_POR_VENCER,
                titulo=f"Presupuesto #{presupuesto.numero} por vencer",
                descripcion=(
                    f"Presupuesto #{presupuesto.numero} ({presupuesto.cliente}) vence el "
                    f"{presupuesto.fecha_vencimiento:%d/%m/%Y}."
                ),
                asignado_a=presupuesto.creado_por,
                presupuesto=presupuesto,
            )
            total += 1

        self.stdout.write(self.style.SUCCESS(f"{total} aviso(s) de presupuesto por vencer generado(s)."))
