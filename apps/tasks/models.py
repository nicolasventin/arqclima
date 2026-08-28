from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.stock.models import Deposito


class PrioridadTarea(models.TextChoices):
    BAJA = "baja", "Baja"
    MEDIA = "media", "Media"
    ALTA = "alta", "Alta"


class EstadoTarea(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    EN_PROCESO = "en_proceso", "En proceso"
    COMPLETADA = "completada", "Completada"


class TipoAutomatizacion(models.TextChoices):
    """Regla de negocio 17 (Etapa 9): las 3 reglas que generan Tarea solas, sin que nadie la cree a mano."""

    SEGUIMIENTO_PRESUPUESTO = "seguimiento_presupuesto", "Seguimiento de presupuesto enviado"
    PRESUPUESTO_POR_VENCER = "presupuesto_por_vencer", "Presupuesto por vencer"
    STOCK_MINIMO = "stock_minimo", "Stock por debajo del mínimo"


class Tarea(models.Model):
    """
    Regla de negocio 14. Una tarea es siempre de una sola persona (no
    M2M): diluir la responsabilidad entre varios asignados no resuelve
    nada que no resuelva mejor crear una Tarea por persona.

    El historial de cada cambio de estado (quién, cuándo) no se guarda
    acá: se reutiliza el AuditLog genérico de la Etapa 1 vía
    apps.tasks.services.cambiar_estado_tarea(), igual que se hizo con
    Presupuesto en la Etapa 5. `completada_en` es la única excepción:
    un atajo de lectura para no tener que ir a buscar el AuditLog cada
    vez que se quiere mostrar "completada hace 3 días" en una lista.
    """

    titulo = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True)
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tareas_asignadas",
    )
    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tareas_creadas",
    )
    fecha_limite = models.DateField(null=True, blank=True)
    prioridad = models.CharField(
        max_length=10, choices=PrioridadTarea.choices, default=PrioridadTarea.MEDIA
    )
    estado = models.CharField(
        max_length=20, choices=EstadoTarea.choices, default=EstadoTarea.PENDIENTE
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    completada_en = models.DateTimeField(null=True, blank=True)

    generada_por = models.CharField(
        max_length=30,
        choices=TipoAutomatizacion.choices,
        blank=True,
        default="",
        help_text="Vacío = la creó una persona. Con valor = generada sola por una de las 3 reglas de la Etapa 9.",
    )
    presupuesto = models.ForeignKey(
        "quotes.Presupuesto",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tareas_generadas",
    )
    producto = models.ForeignKey(
        "catalog.Producto",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tareas_stock_minimo",
    )
    deposito = models.CharField(max_length=20, choices=Deposito.choices, blank=True, default="")

    class Meta:
        ordering = ["fecha_limite", "-prioridad"]
        verbose_name = "Tarea"
        verbose_name_plural = "Tareas"
        permissions = [
            ("view_all_tareas", "Puede ver las tareas de todo el equipo, no solo las propias"),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(generada_por="", presupuesto__isnull=True, producto__isnull=True)
                    | Q(
                        generada_por__in=[
                            TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
                            TipoAutomatizacion.PRESUPUESTO_POR_VENCER,
                        ],
                        presupuesto__isnull=False,
                        producto__isnull=True,
                    )
                    | Q(
                        generada_por=TipoAutomatizacion.STOCK_MINIMO,
                        producto__isnull=False,
                        presupuesto__isnull=True,
                    )
                ),
                name="tarea_generada_por_coherente_con_campos",
            ),
        ]

    def __str__(self):
        return self.titulo

    @property
    def esta_vencida(self):
        """
        No es un estado (la regla 14 define la máquina de estados con
        solo tres: Pendiente/En proceso/Completada) — es un cálculo de
        lectura, sin persistir ni requerir ningún comando periódico,
        porque a diferencia de Presupuesto acá no hay ningún estado
        "Vencida" al que transicionar.
        """
        return (
            self.fecha_limite is not None
            and self.fecha_limite < timezone.localdate()
            and self.estado != EstadoTarea.COMPLETADA
        )
