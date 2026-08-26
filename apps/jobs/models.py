from django.conf import settings
from django.db import models


class EstadoTrabajo(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    PREPARANDO_MATERIALES = "preparando_materiales", "Preparando materiales"
    LISTO = "listo", "Listo"
    EN_EJECUCION = "en_ejecucion", "En ejecución"
    TERMINADO = "terminado", "Terminado"


# Orden real de avance (regla de negocio 10). El estado de un Trabajo
# SOLO avanza, nunca retrocede, pero puede saltear etapas (ej. Pendiente
# → Listo directo si ya había stock disponible) — a diferencia de
# Presupuesto/Tarea, acá no hace falta un grafo de pares explícitos:
# alcanza con comparar la posición en esta lista (ver
# apps.jobs.services.cambiar_estado_trabajo).
ORDEN_ESTADOS = [
    EstadoTrabajo.PENDIENTE,
    EstadoTrabajo.PREPARANDO_MATERIALES,
    EstadoTrabajo.LISTO,
    EstadoTrabajo.EN_EJECUCION,
    EstadoTrabajo.TERMINADO,
]


class Trabajo(models.Model):
    """
    Regla de negocio 10: un Trabajo nace de un Presupuesto Aceptado
    (uno solo por presupuesto — OneToOne), heredando cliente (vía la
    relación con Presupuesto, no duplicado), dirección y observaciones
    (copiadas al crear, pero editables después de forma independiente:
    un trabajo puede necesitar una nota operativa distinta de la
    comercial). La creación NO es automática al aceptar el presupuesto
    — es una acción separada y explícita (ver
    apps.jobs.services.crear_trabajo), coherente con el resto del
    proyecto: ninguna transición de estado dispara efectos secundarios
    ocultos sin confirmación humana.
    """

    presupuesto = models.OneToOneField(
        "quotes.Presupuesto", on_delete=models.PROTECT, related_name="trabajo"
    )
    tecnico_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trabajos_asignados",
    )
    direccion = models.CharField(max_length=255, blank=True)
    observaciones = models.TextField(blank=True)
    estado = models.CharField(
        max_length=30, choices=EstadoTrabajo.choices, default=EstadoTrabajo.PENDIENTE
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="trabajos_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Trabajo"
        verbose_name_plural = "Trabajos"
        permissions = [
            (
                "manage_preparacion",
                "Puede cambiar el estado de un trabajo a Preparando materiales / Listo",
            ),
            (
                "manage_ejecucion_propia",
                "Puede cambiar el estado de sus propios trabajos asignados a En ejecución / Terminado",
            ),
        ]

    def __str__(self):
        return f"Trabajo #{self.pk} — {self.presupuesto.cliente}"
