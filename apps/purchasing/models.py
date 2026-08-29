from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL

from apps.stock.models import Deposito


class EstadoOrdenCompra(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    EMITIDA = "emitida", "Emitida"
    ENVIADA = "enviada", "Enviada"
    RECEPCION_PARCIAL = "recepcion_parcial", "Recepción parcial"
    RECIBIDA = "recibida", "Recibida"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


# Las transiciones a RECEPCION_PARCIAL y RECIBIDA son automáticas desde
# recibir_linea(); CERRADA se ejecuta mediante cerrar_orden().
TRANSICIONES_VALIDAS = {
    EstadoOrdenCompra.BORRADOR: {EstadoOrdenCompra.EMITIDA},
    EstadoOrdenCompra.EMITIDA: {
        EstadoOrdenCompra.BORRADOR,
        EstadoOrdenCompra.ENVIADA,
        EstadoOrdenCompra.CANCELADA,
    },
    EstadoOrdenCompra.ENVIADA: {
        EstadoOrdenCompra.RECEPCION_PARCIAL,
        EstadoOrdenCompra.RECIBIDA,
        EstadoOrdenCompra.CANCELADA,
    },
    EstadoOrdenCompra.RECEPCION_PARCIAL: {
        EstadoOrdenCompra.RECIBIDA,
        EstadoOrdenCompra.CERRADA,
    },
    EstadoOrdenCompra.RECIBIDA: {EstadoOrdenCompra.CERRADA},
    EstadoOrdenCompra.CERRADA: set(),
    EstadoOrdenCompra.CANCELADA: set(),
}


class OrdenDeCompra(models.Model):
    """
    Ciclo de compra simplificado desde la Etapa 11K:

    Borrador → Emitida → Enviada → Recepción parcial → Recibida → Cerrada.

    Ya no existe aprobación obligatoria. Emitir congela el contenido
    porque las líneas solo son editables en Borrador (trigger PostgreSQL).
    Antes de enviarla todavía puede volver a Borrador para corregirse,
    quedando esa reapertura registrada en AuditLog.

    Cancelada es una rama lateral válida antes de cualquier recepción.
    Una vez recibida mercadería, si el proveedor no entregará el remanente,
    la orden se cierra como recepción parcial dejando motivo.

    Los campos legacy de aprobación se conservan por compatibilidad
    histórica: no participan del flujo nuevo y permiten no destruir
    trazabilidad de órdenes creadas antes de 11K.
    """

    numero = models.PositiveIntegerField(
        unique=True,
        editable=False,
        db_default=RawSQL("nextval('purchasing_ordendecompra_numero_seq')", []),
    )
    proveedor = models.ForeignKey(
        "catalog.Proveedor", on_delete=models.PROTECT, related_name="ordenes_compra"
    )
    deposito_destino = models.CharField(max_length=20, choices=Deposito.choices)
    estado = models.CharField(
        max_length=30, choices=EstadoOrdenCompra.choices, default=EstadoOrdenCompra.BORRADOR
    )
    notas = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    emitida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_emitidas",
    )
    emitida_en = models.DateTimeField(null=True, blank=True)

    # Legacy pre-11K. Se mantienen para conservar historial previo.
    solicitud_aprobacion_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_solicitadas_aprobacion",
    )
    solicitud_aprobacion_en = models.DateTimeField(null=True, blank=True)

    aprobada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_aprobadas",
    )
    aprobada_en = models.DateTimeField(null=True, blank=True)

    rechazada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_rechazadas",
    )
    rechazada_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    enviada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_enviadas",
    )
    enviada_en = models.DateTimeField(null=True, blank=True)

    primera_recepcion_en = models.DateTimeField(null=True, blank=True)
    recibida_en = models.DateTimeField(null=True, blank=True)

    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_cerradas",
    )
    cerrada_en = models.DateTimeField(null=True, blank=True)
    motivo_cierre = models.TextField(blank=True)

    cancelada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ordenes_compra_canceladas",
    )
    cancelada_en = models.DateTimeField(null=True, blank=True)
    motivo_cancelacion = models.TextField(blank=True)

    class Meta:
        ordering = ["-numero"]
        verbose_name = "Orden de compra"
        verbose_name_plural = "Órdenes de compra"

    def __str__(self):
        return f"OC #{self.numero} — {self.proveedor}"


class LineaOrdenCompra(models.Model):
    """
    producto_proveedor resuelve explícitamente de qué proveedor puntual
    sale cada línea. costo_esperado se sugiere desde pricing pero sigue
    siendo editable mientras la orden está en Borrador.

    Dos garantías viven en triggers de Postgres:
    - la línea solo se puede editar/crear/borrar si la orden está en Borrador;
    - producto_proveedor.proveedor debe coincidir con orden.proveedor.
    """

    orden = models.ForeignKey(OrdenDeCompra, on_delete=models.CASCADE, related_name="lineas")
    producto_proveedor = models.ForeignKey(
        "catalog.ProductoProveedor", on_delete=models.PROTECT, related_name="lineas_orden_compra"
    )
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    costo_esperado = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["orden_id", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(cantidad__gt=0),
                name="lineaordencompra_cantidad_positiva",
            ),
            models.CheckConstraint(
                check=models.Q(costo_esperado__gte=0),
                name="lineaordencompra_costo_no_negativo",
            ),
        ]
        verbose_name = "Línea de orden de compra"
        verbose_name_plural = "Líneas de orden de compra"

    def __str__(self):
        return f"{self.producto_proveedor} x{self.cantidad}"
