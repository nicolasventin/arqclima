from django.conf import settings
from django.db import models

from apps.catalog.models import ProductoProveedor


class Moneda(models.TextChoices):
    ARS = "ars", "Pesos (ARS)"
    USD = "usd", "Dólares (USD)"


class HistorialCosto(models.Model):
    """
    Historial de costos — regla de negocio 3: nunca se borra un costo
    viejo. Es append-only por dos capas:

    1. A nivel de aplicación: la única forma soportada de escribir acá es
       apps.pricing.services.registrar_costo(), que siempre hace .create().
       Ningún grupo tiene los permisos change_historialcosto /
       delete_historialcosto (ver migración 0003_asignar_permisos_roles).
    2. A nivel de base de datos: un trigger de Postgres (migración
       0002_trigger_historialcosto_inmutable) rechaza cualquier UPDATE o
       DELETE sobre esta tabla, sin importar el origen (ORM, admin, SQL
       directo, un bug futuro).

    El costo vigente de un ProductoProveedor nunca es un campo que se pisa:
    es la fila más reciente por `vigente_desde`.
    """

    producto_proveedor = models.ForeignKey(
        ProductoProveedor, on_delete=models.PROTECT, related_name="historial_costos"
    )
    costo = models.DecimalField(max_digits=12, decimal_places=2)
    moneda = models.CharField(max_length=3, choices=Moneda.choices, default=Moneda.ARS)
    vigente_desde = models.DateTimeField(auto_now_add=True)
    cargado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="costos_cargados",
    )
    origen = models.CharField(max_length=50, default="manual")

    class Meta:
        ordering = ["-vigente_desde"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(costo__gte=0),
                name="historialcosto_costo_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(moneda__in=Moneda.values),
                name="historialcosto_moneda_valida",
            ),
        ]
        verbose_name = "Historial de costo"
        verbose_name_plural = "Historial de costos"
        permissions = [
            (
                "view_precio_repuestos",
                "Puede ver precios de productos de la línea de repuestos",
            ),
            (
                "manage_costos_repuestos",
                "Puede registrar costos nuevos para productos de la línea de repuestos",
            ),
        ]

    def __str__(self):
        etiqueta = "U$S" if self.moneda == Moneda.USD else "$"
        return f"{self.producto_proveedor} — {etiqueta} {self.costo} ({self.vigente_desde:%Y-%m-%d})"


class ConfiguracionGeneral(models.Model):
    """
    Fila única (singleton, pk=1 forzado) con los parámetros globales de
    precios. margen_general es el piso de la jerarquía de márgenes
    (producto > marca > categoría > general); flete y costo financiero solo
    existen a nivel general (no son configurables por marca/categoría/
    producto, a diferencia del margen).
    """

    margen_general = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    flete_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    costo_financiero_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    iva_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=21,
        help_text="Alícuota de IVA (%) para ítems de presupuesto con tipo de IVA '+ IVA'.",
    )
    margen_minimo_alerta = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=15,
        help_text="Si un descuento deja el margen por debajo de este %, se muestra una alerta (no bloquea).",
    )
    margen_mano_obra = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=(
            "Margen (%) por defecto para el concepto manual 'Mano de obra' "
            "en presupuestos. No es un producto de catálogo, por eso vive "
            "acá y no en la jerarquía Producto/Marca/Categoría."
        ),
    )
    dias_seguimiento_presupuesto_enviado = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Días sin respuesta desde que se envió un presupuesto para que "
            "el sistema genere una tarea de seguimiento automática (Etapa 9)."
        ),
    )
    dias_aviso_presupuesto_por_vencer = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Días de anticipación a fecha_vencimiento para avisarle al "
            "vendedor que un presupuesto Enviado está por vencer (Etapa 9)."
        ),
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(margen_general__gte=0),
                name="config_margen_general_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(margen_mano_obra__gte=0),
                name="config_margen_mano_obra_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(flete_pct__gte=0),
                name="config_flete_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(costo_financiero_pct__gte=0),
                name="config_costo_financiero_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(margen_minimo_alerta__gte=0),
                name="config_margen_alerta_no_negativo",
            ),
            models.CheckConstraint(
                check=models.Q(iva_pct__gte=0) & models.Q(iva_pct__lte=100),
                name="config_iva_pct_0_100",
            ),
            models.CheckConstraint(
                check=models.Q(dias_seguimiento_presupuesto_enviado__gt=0),
                name="config_dias_seguimiento_positivo",
            ),
            models.CheckConstraint(
                check=models.Q(dias_aviso_presupuesto_por_vencer__gt=0),
                name="config_dias_aviso_positivo",
            ),
        ]
        verbose_name = "Configuración general de precios"
        verbose_name_plural = "Configuración general de precios"
        permissions = [
            ("manage_margenes", "Puede configurar márgenes, flete y costo financiero"),
        ]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def obtener(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Configuración general de precios"
