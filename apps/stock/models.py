from django.conf import settings
from django.db import models


class Deposito(models.TextChoices):
    GENERAL = "general", "Stock general (obra)"
    REPUESTOS = "repuestos", "Stock de repuestos (service)"


class TipoMovimiento(models.TextChoices):
    ENTRADA = "entrada", "Entrada"
    SALIDA = "salida", "Salida"
    AJUSTE = "ajuste", "Ajuste"
    DEVOLUCION = "devolucion", "Devolución"


class MovimientoStock(models.Model):
    """
    Ledger append-only — mismo criterio que HistorialCosto en la Etapa 3:
    el stock actual de un producto en un depósito NUNCA es un campo que
    se pisa, es SUM(cantidad) de sus movimientos. Un trigger de Postgres
    (migración 0002) rechaza cualquier UPDATE/DELETE, igual que con
    HistorialCosto.

    `cantidad` se guarda CON SIGNO (positivo suma stock, negativo resta).
    `tipo` es una etiqueta de categorización para reportes/auditoría, no
    algo que haya que reinterpretar en cada consulta — el signo ya es
    la fuente de verdad de si sube o baja el stock.

    General vs. repuestos (regla de negocio 12) es un solo modelo con
    el campo `deposito`, no dos modelos: un mismo Producto puede tener
    stock en los dos depósitos a la vez, la separación es sobre qué
    pool de unidades físicas, no sobre la identidad del producto. El
    control de acceso distinto por depósito se resuelve con permisos
    (ver apps.stock.permissions), no con tablas separadas.

    `referencia_libre` (texto) sigue existiendo para el caso genérico
    sin objeto vinculado (compras, ajustes). `trabajo`/`material_trabajo`
    (Etapa 8, Parte 3) son las FKs reales y nullable que reemplazan la
    necesidad de referencia_libre para la conexión con Trabajo — se
    había evaluado un GenericForeignKey (Content Types) en la Etapa 7,
    pero un GFK es content_type_id + object_id sin ningún FOREIGN KEY
    real que Postgres pueda verificar; se prefirió esta FK real con su
    propia migración cuando el modelo Trabajo existiera, mismo camino
    que ItemPresupuesto.producto_proveedor en la Etapa 5.
    """

    producto = models.ForeignKey(
        "catalog.Producto", on_delete=models.PROTECT, related_name="movimientos_stock"
    )
    deposito = models.CharField(max_length=20, choices=Deposito.choices)
    tipo = models.CharField(max_length=20, choices=TipoMovimiento.choices)
    cantidad = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Con signo: positivo suma stock, negativo resta.",
    )
    requiere_devolucion = models.BooleanField(
        default=False,
        help_text=(
            "Solo tiene sentido en una Salida de repuestos: marca que ese material "
            "queda 'pendiente de devolución' hasta que se registre qué volvió "
            "(regla de negocio 11)."
        ),
    )
    salida_relacionada = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="devoluciones",
        help_text="Solo se completa en una Devolución: la Salida de repuestos que resuelve.",
    )

    referencia_libre = models.CharField(
        max_length=255,
        blank=True,
        help_text="Descripción libre del origen/destino cuando todavía no hay un objeto vinculado.",
    )
    trabajo = models.ForeignKey(
        "jobs.Trabajo",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movimientos_stock",
    )
    material_trabajo = models.ForeignKey(
        "jobs.MaterialTrabajo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_stock",
        help_text=(
            "Línea puntual del listado de materiales que originó este movimiento. "
            "SET_NULL (no PROTECT): si la línea se borra después, el movimiento ya "
            "hecho no se pierde, solo pierde el puntero fino a esa línea."
        ),
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="movimientos_stock"
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Movimiento de stock"
        verbose_name_plural = "Movimientos de stock"
        permissions = [
            (
                "manage_stock_general",
                "Puede registrar entradas y salidas en el stock general (obra)",
            ),
            ("ajustar_stock_general", "Puede hacer ajustes manuales en el stock general"),
            (
                "manage_stock_repuestos",
                "Puede registrar entradas y salidas en el stock de repuestos (service)",
            ),
            (
                "manage_stock_minimo",
                "Puede configurar el stock mínimo de alerta por producto",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(tipo=TipoMovimiento.ENTRADA, cantidad__gt=0)
                    | models.Q(tipo=TipoMovimiento.SALIDA, cantidad__lt=0)
                    | models.Q(tipo=TipoMovimiento.DEVOLUCION, cantidad__gt=0)
                    | models.Q(tipo=TipoMovimiento.AJUSTE)
                ),
                name="movimientostock_signo_coherente_con_tipo",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(requiere_devolucion=False)
                    | models.Q(tipo=TipoMovimiento.SALIDA, deposito=Deposito.REPUESTOS)
                ),
                name="movimientostock_requiere_devolucion_solo_en_salida_repuestos",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(salida_relacionada__isnull=True)
                    | models.Q(tipo=TipoMovimiento.DEVOLUCION)
                ),
                name="movimientostock_salida_relacionada_solo_en_devolucion",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.cantidad} — {self.producto} ({self.get_deposito_display()})"
