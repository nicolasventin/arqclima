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
    CANCELADA = "cancelada", "Cancelada"


# Grafo explícito (a diferencia de Trabajo, acá no hay un único orden de
# avance — Rechazada y Cancelada son ramas laterales, no posiciones en
# una secuencia) — mismo criterio que Presupuesto en la Etapa 5.
TRANSICIONES_VALIDAS = {
    EstadoOrdenCompra.BORRADOR: {EstadoOrdenCompra.PENDIENTE_APROBACION},
    EstadoOrdenCompra.PENDIENTE_APROBACION: {
        EstadoOrdenCompra.APROBADA,
        EstadoOrdenCompra.RECHAZADA,
        EstadoOrdenCompra.BORRADOR,
        EstadoOrdenCompra.CANCELADA,
    },
    EstadoOrdenCompra.RECHAZADA: {EstadoOrdenCompra.BORRADOR},
    EstadoOrdenCompra.APROBADA: {EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.CANCELADA},
    EstadoOrdenCompra.ENVIADA: {EstadoOrdenCompra.CANCELADA},
    EstadoOrdenCompra.CANCELADA: set(),
}


class OrdenDeCompra(models.Model):
    """
    Regla de negocio 7: Rodrigo, Gabriel y Andrés pueden crear órdenes
    de compra, pero Diego tiene que aprobarlas antes de que se envíen
    al proveedor — bloqueo real, no una alerta. El bloqueo es
    estructural: `Enviada` solo es alcanzable desde `Aprobada` en
    TRANSICIONES_VALIDAS, nunca directo desde Borrador/Pendiente, y la
    transición puntual Pendiente→Aprobada tiene su propio permiso
    exclusivo de Diego (ver apps.purchasing.permissions).

    `numero` sale de una secuencia de Postgres (migración 0001), mismo
    criterio que Presupuesto.numero en la Etapa 5 — nunca max()+1.

    `deposito_destino` es un campo explícito, no inferido del rol de
    quien crea la orden: Diego puede crear/aprobar cualquier orden y
    no tiene un depósito "por defecto" implícito.

    Aprobada/Enviada NO vuelven a Borrador — mismo motivo que
    Presupuesto.Aceptado: si se pudiera reabrir después de aprobada, la
    aprobación de Diego quedaría aplicada a datos que ya cambiaron. Si
    hace falta modificar algo después de aprobada, se cancela y se
    crea una orden nueva.
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
    `producto_proveedor` resuelve explícitamente de qué proveedor
    puntual sale cada línea (regla de negocio 2: nunca auto-elegir),
    mismo mecanismo que ItemPresupuesto.producto_proveedor.

    `costo_esperado` se sugiere desde pricing.services.costo_actual()
    al agregar la línea, pero es editable — nunca se autocompleta sin
    mostrar (regla 4).

    Dos garantías viven en triggers de Postgres (migración 0002), no
    en validación de aplicación:
    - la línea solo se puede editar/crear/borrar si la orden dueña
      está en Borrador (mismo patrón que ItemPresupuesto en quotes);
    - producto_proveedor.proveedor tiene que coincidir siempre con
      orden.proveedor (comparación cross-table, no expresable con un
      CheckConstraint simple).
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
