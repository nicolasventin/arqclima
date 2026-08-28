from django.db import models


class Reportes(models.Model):
    """
    Sin campos de negocio ni datos propios: los reportes son consultas
    sobre datos que ya viven en otras apps (Presupuesto, MovimientoStock,
    etc.), calculadas al pedirlas — mismo criterio que stock_actual()
    o Tarea.esta_vencida, nunca persistidas acá.

    Este modelo existe únicamente para poder declarar permisos custom
    que no pertenecen a ningún modelo de negocio concreto (patrón
    documentado por Django para permisos "sueltos"). Los permisos de
    cada área de reporte se agregan en la Parte de la Etapa 9 que la
    implementa, no todos de una — mismo criterio que el resto del
    proyecto para no precargar permisos de funcionalidad inexistente.
    """

    class Meta:
        default_permissions = ()
        permissions = [
            ("view_reporte_comercial", "Puede ver el reporte comercial"),
            (
                "view_montos_confidenciales",
                "Puede ver montos agregados de facturación/ingresos en los reportes",
            ),
        ]
