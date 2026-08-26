from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado


class Command(BaseCommand):
    """
    Marca como Vencido todo presupuesto Enviado cuya fecha_vencimiento
    ya pasó. Pensado para correrse periódicamente (cron del SO); no hay
    scheduler (Celery/etc.) en la infraestructura del proyecto todavía.
    Es idempotente: un presupuesto ya Vencido no vuelve a matchear el
    filtro, así que correrlo de más no rompe nada.
    """

    help = "Marca como Vencido los presupuestos Enviados cuya fecha_vencimiento ya pasó."

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        candidatos = Presupuesto.objects.filter(
            estado=EstadoPresupuesto.ENVIADO, fecha_vencimiento__lt=hoy
        )
        total = 0
        for presupuesto in candidatos:
            cambiar_estado(
                presupuesto,
                EstadoPresupuesto.VENCIDO,
                usuario=None,
                accion="vencer_presupuesto",
                detalle="Vencimiento automático (fecha_vencimiento superada).",
            )
            total += 1

        self.stdout.write(self.style.SUCCESS(f"{total} presupuesto(s) marcados como Vencido."))
