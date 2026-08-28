from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.pricing.models import ConfiguracionGeneral
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.tasks.models import Tarea, TipoAutomatizacion
from apps.tasks.services import crear_tarea_automatica

ACCIONES_ENVIO = ["enviar_presupuesto", "enviar_presupuesto_margen_bajo"]


class Command(BaseCommand):
    """
    Regla de negocio 17: avisa al vendedor (Presupuesto.creado_por,
    no hay un campo "vendedor" separado) cuando un presupuesto Enviado
    está a N días o menos de vencer (ConfiguracionGeneral.
    dias_aviso_presupuesto_por_vencer).

    Idempotente por (presupuesto, posterior al último envío en
    AuditLog) — mismo criterio que generar_seguimiento_presupuestos.
    Corregido a partir de una primera versión que ataba la
    idempotencia a "existió alguna vez una tarea de este tipo", sin
    ventana: reabrir un presupuesto, cambiarle fecha_vencimiento y
    reenviarlo es parte normal del ciclo de vida de Presupuesto desde
    la Etapa 5 (renegociar el plazo con un cliente) — un reenvío tiene
    que volver a habilitar el aviso para la fecha nueva, no quedar
    callado para siempre por un aviso viejo sobre una fecha que ya no
    aplica. Pensado para cron diario, sin scheduler propio todavía.
    """

    help = "Avisa al vendedor de presupuestos Enviados que están por vencer."

    def handle(self, *args, **options):
        dias = ConfiguracionGeneral.obtener().dias_aviso_presupuesto_por_vencer
        hoy = timezone.localdate()
        limite_fecha = hoy + timezone.timedelta(days=dias)
        content_type = ContentType.objects.get_for_model(Presupuesto)

        candidatos = Presupuesto.objects.filter(
            estado=EstadoPresupuesto.ENVIADO,
            fecha_vencimiento__isnull=False,
            fecha_vencimiento__gte=hoy,
            fecha_vencimiento__lte=limite_fecha,
        )

        total = 0
        for presupuesto in candidatos:
            ultimo_envio = (
                AuditLog.objects.filter(
                    content_type=content_type,
                    object_id=str(presupuesto.pk),
                    accion__in=ACCIONES_ENVIO,
                )
                .order_by("-creado_en")
                .first()
            )
            if ultimo_envio is None:
                continue

            ya_existe = Tarea.objects.filter(
                generada_por=TipoAutomatizacion.PRESUPUESTO_POR_VENCER,
                presupuesto=presupuesto,
                creado_en__gte=ultimo_envio.creado_en,
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
