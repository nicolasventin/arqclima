from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditLog(models.Model):
    """
    Registro genérico de auditoría, reutilizable desde cualquier app futura
    vía apps.audit.services.log_action(). Usa GenericForeignKey para poder
    auditar cualquier entidad (usuario, presupuesto, orden de compra,
    movimiento de stock, etc.) sin acoplarse a un modelo concreto.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acciones_auditadas",
    )
    accion = models.CharField(max_length=100)
    detalle = models.TextField(blank=True)

    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    object_id = models.CharField(max_length=255, null=True, blank=True)
    objeto_repr = models.CharField(max_length=255, blank=True)
    objeto = GenericForeignKey("content_type", "object_id")

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"

    def __str__(self):
        quien = self.usuario or "Sistema"
        return f"[{self.creado_en:%Y-%m-%d %H:%M}] {quien} — {self.accion}"
