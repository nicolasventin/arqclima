from django.conf import settings
from django.db import models
from django.utils import timezone


class PrioridadTarea(models.TextChoices):
    BAJA = "baja", "Baja"
    MEDIA = "media", "Media"
    ALTA = "alta", "Alta"


class EstadoTarea(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    EN_PROCESO = "en_proceso", "En proceso"
    COMPLETADA = "completada", "Completada"


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

    class Meta:
        ordering = ["fecha_limite", "-prioridad"]
        verbose_name = "Tarea"
        verbose_name_plural = "Tareas"
        permissions = [
            ("view_all_tareas", "Puede ver las tareas de todo el equipo, no solo las propias"),
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
