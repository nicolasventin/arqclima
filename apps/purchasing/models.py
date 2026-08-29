from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL

from apps.stock.models import Deposito


class EstadoOrdenCompra(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    PENDIENTE_APROBACION = "pendiente_aprobacion", "Pendiente de aprobación"
    APROBADA = "aprobada", "Aprobada"
    RECHAZADA = "rechazada", "Rechazada"
    ENVIADA = "enviada", "Enviada"
    RECEPCION_PARCIAL = "recepcion_parcial", "Recepción parcial"
    RECIBIDA = "recibida", "Recibida"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


# Grafo de ciclo de vida. Las transiciones a RECEPCION_PARCIAL y
# RECIBIDA son automáticas desde recibir_linea(); CERRADA se ejecuta
# mediante cerrar_orden(). El resto son acciones explícitas del usuario.
TRANSICIONES_VALIDAS = {
    EstadoOrdenCompra.BORRADOR: {EstadoOrdenCompra.PENDIENTE_APROBACION},
    EstadoOrdenCompra.PENDIENTE_APROBACION: {
        EstadoOrdenCompra.APROBADA,
        EstadoOrdenCompra.RECHAZADA,
        EstadoOrdenCompra.BORRADOR,
        EstadoOrdenCompra.CANCELADA,
    },
    EstadoOrdenCompra.RECHAZADA: {EstadoOrdenCompra.BORRADOR},
    EstadoOrdenCompra.APROBADA: {
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
    Ciclo de compra:
    Borrador → Pendiente de aprobación → Aprobada → Enviada →
    Recepción parcial → Recibida → Cerrada.

    Rechazada y Cancelada son ramas laterales. Diego debe aprobar antes
    de que la orden pueda enviarse al proveedor. Una vez recibida
    mercadería, la orden ya no se cancela: si el proveedor no entregará
    el remanente, se cierra como recepción parcial dejando motivo.

    Las líneas solo son editables en Borrador (trigger de PostgreSQL).
    Por eso una aprobación siempre corresponde exactamente al contenido
    que Diego revisó.

    Los campos de usuario/fecha guardan el hito operativo actual de
    manera consultable. El historial completo sigue viviendo en AuditLog.
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
        permissions = [
            ("approve_ordendecompra", "Puede aprobar o rechazar una orden de compra"),
        ]

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
