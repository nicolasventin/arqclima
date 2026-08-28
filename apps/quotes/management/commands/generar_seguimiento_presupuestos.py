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
    Regla de negocio 17: genera una tarea de seguimiento para todo
    presupuesto Enviado que lleva N días (ConfiguracionGeneral.
    dias_seguimiento_presupuesto_enviado) sin respuesta.

    No hay un campo fecha_envio en Presupuesto: la referencia es el
    AuditLog que enviar_presupuesto() ya deja en CADA transición a
    Enviado (decisión de la Etapa 5, incluye reenvíos) — evita duplicar
    esa fecha en un campo nuevo. Idempotente por presupuesto: no crea
    una segunda tarea posterior al mismo envío; un reenvío sí vuelve a
    habilitar la ventana. Pensado para cron diario, sin scheduler
    propio en la infraestructura todavía (mismo criterio que
    vencer_presupuestos).
    """

    help = "Genera tareas de seguimiento para presupuestos Enviados sin respuesta hace N días."

    def handle(self, *args, **options):
        dias = ConfiguracionGeneral.obtener().dias_seguimiento_presupuesto_enviado
        limite = timezone.now() - timezone.timedelta(days=dias)
        content_type = ContentType.objects.get_for_model(Presupuesto)

        total = 0
        for presupuesto in Presupuesto.objects.filter(estado=EstadoPresupuesto.ENVIADO):
            ultimo_envio = (
                AuditLog.objects.filter(
                    content_type=content_type,
                    object_id=str(presupuesto.pk),
                    accion__in=ACCIONES_ENVIO,
                )
                .order_by("-creado_en")
                .first()
            )
            if ultimo_envio is None or ultimo_envio.creado_en > limite:
                continue

            ya_existe = Tarea.objects.filter(
                generada_por=TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
                presupuesto=presupuesto,
                creado_en__gte=ultimo_envio.creado_en,
            ).exists()
            if ya_existe:
                continue

            crear_tarea_automatica(
                TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
                titulo=f"Seguimiento presupuesto #{presupuesto.numero}",
                descripcion=(
                    f"Presupuesto #{presupuesto.numero} ({presupuesto.cliente}) enviado hace "
                    f"{dias}+ días sin respuesta. Contactar al cliente."
                ),
                asignado_a=presupuesto.creado_por,
                presupuesto=presupuesto,
            )
            total += 1

        self.stdout.write(self.style.SUCCESS(f"{total} tarea(s) de seguimiento generada(s)."))
