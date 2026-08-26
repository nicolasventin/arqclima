from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL


class PlantillaCondiciones(models.Model):
    """
    Bloque de condiciones (exclusiones, garantía, forma de pago) que se
    precarga en Presupuesto.condiciones al crear un presupuesto nuevo.
    A partir de ahí, el texto queda copiado y editable por presupuesto:
    cambiar la plantilla después no afecta presupuestos ya creados.
    """

    nombre = models.CharField(max_length=150)
    texto = models.TextField()
    predeterminada = models.BooleanField(
        default=False,
        help_text="Se precarga automáticamente al crear un presupuesto nuevo.",
    )
    activa = models.BooleanField(
        default=True,
        help_text="Una plantilla inactiva no se ofrece para presupuestos nuevos, pero no se borra (conserva el histórico de los presupuestos que ya la usaron).",
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["predeterminada"],
                condition=models.Q(predeterminada=True),
                name="una_sola_plantilla_predeterminada",
            ),
        ]
        verbose_name = "Plantilla de condiciones"
        verbose_name_plural = "Plantillas de condiciones"

    def __str__(self):
        return self.nombre


class EstadoPresupuesto(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    ENVIADO = "enviado", "Enviado"
    ACEPTADO = "aceptado", "Aceptado"
    RECHAZADO = "rechazado", "Rechazado"
    VENCIDO = "vencido", "Vencido"
    CANCELADO = "cancelado", "Cancelado"


class TipoDescuento(models.TextChoices):
    PORCENTAJE = "porcentaje", "Porcentaje"
    MONTO = "monto", "Monto fijo"


class Presupuesto(models.Model):
    """
    `numero` se genera con la secuencia de Postgres
    quotes_presupuesto_numero_seq (creada en la migración 0001_initial),
    no con max(numero)+1: dos presupuestos creados en simultáneo no
    pueden terminar con el mismo número.

    La dirección vive acá y no en Cliente: un mismo cliente puede pedir
    presupuestos para distintas obras/direcciones.

    Los precios de los ítems quedan congelados al crear el presupuesto
    (regla de negocio 8): este modelo no recalcula nada a partir del
    catálogo/pricing una vez creado.
    """

    numero = models.PositiveIntegerField(
        unique=True,
        editable=False,
        db_default=RawSQL("nextval('quotes_presupuesto_numero_seq')", []),
    )
    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.PROTECT, related_name="presupuestos"
    )
    direccion = models.CharField(
        max_length=255,
        blank=True,
        help_text="Dirección de la obra para este presupuesto puntual.",
    )
    fecha = models.DateField(auto_now_add=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=20, choices=EstadoPresupuesto.choices, default=EstadoPresupuesto.BORRADOR
    )
    cantidad_unidades = models.PositiveIntegerField(
        default=1,
        help_text="Para multiplicar el total cuando se cotiza por unidad habitacional.",
    )
    descuento_general_tipo = models.CharField(
        max_length=20, choices=TipoDescuento.choices, default=TipoDescuento.PORCENTAJE
    )
    descuento_general_valor = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notas_generales = models.TextField(blank=True)
    plantilla_condiciones = models.ForeignKey(
        PlantillaCondiciones,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="presupuestos",
    )
    condiciones = models.TextField(
        blank=True,
        help_text="Copiado desde la plantilla al crear el presupuesto; a partir de ahí es propio de este presupuesto.",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="presupuestos_creados",
    )

    class Meta:
        ordering = ["-numero"]
        verbose_name = "Presupuesto"
        verbose_name_plural = "Presupuestos"

    def __str__(self):
        return f"Presupuesto #{self.numero} — {self.cliente}"


class SeccionPresupuesto(models.Model):
    """Agrupador opcional de ítems, con título libre (ej. '1era etapa')."""

    presupuesto = models.ForeignKey(
        Presupuesto, on_delete=models.CASCADE, related_name="secciones"
    )
    titulo = models.CharField(max_length=150)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["presupuesto_id", "orden"]
        verbose_name = "Sección de presupuesto"
        verbose_name_plural = "Secciones de presupuesto"

    def __str__(self):
        return f"{self.presupuesto} — {self.titulo}"


class TipoIVA(models.TextChoices):
    INCLUIDO = "incluido", "IVA incluido"
    MAS_IVA = "mas_iva", "+ IVA"


class ItemPresupuesto(models.Model):
    """
    `presupuesto` es siempre obligatorio; `seccion` es opcional para no
    forzar una sección invisible en presupuestos simples que no las usan.

    `opcional` e `incluido` son independientes:
    - opcional=False, incluido=True  -> ítem normal, siempre suma.
    - opcional=True,  incluido=True  -> alternativa activa, suma.
    - opcional=True,  incluido=False -> alternativa ofrecida pero no
      activada, no suma (ej. "mejora recomendada").
    - opcional=False, incluido=False -> inválido: un ítem no opcional
      (obligatorio) no puede estar excluido del total. Prohibido por
      CheckConstraint a nivel de base de datos.
    """

    presupuesto = models.ForeignKey(
        Presupuesto, on_delete=models.CASCADE, related_name="items"
    )
    seccion = models.ForeignKey(
        SeccionPresupuesto,
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
    )
    producto = models.ForeignKey(
        "catalog.Producto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="items_presupuesto",
    )
    descripcion_manual = models.CharField(
        max_length=255,
        blank=True,
        help_text="Para conceptos sin producto de catálogo (ej. mano de obra/instalación).",
    )
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    precio_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Congelado al crear el ítem: no cambia si después cambian los precios del catálogo.",
    )
    costo_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Costo congelado al crear el ítem (mismo criterio que precio_unitario). "
            "En ítems de catálogo sale del costo vigente del proveedor elegido; en "
            "conceptos manuales es opcional y editable (para 'Mano de obra' se puede "
            "sugerir a partir de pricing.ConfiguracionGeneral.margen_mano_obra, ver "
            "apps.quotes.services.sugerir_costo_mano_obra). Sin costo cargado, el "
            "ítem queda fuera del chequeo de margen bajo."
        ),
    )
    descuento_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tipo_iva = models.CharField(max_length=20, choices=TipoIVA.choices, default=TipoIVA.INCLUIDO)
    opcional = models.BooleanField(
        default=False,
        help_text="Ítem alternativo tipo 'mejora recomendada': no suma al total salvo que además esté incluido.",
    )
    incluido = models.BooleanField(
        default=True,
        help_text="Si un ítem opcional está desmarcado acá, no suma al total.",
    )
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["presupuesto_id", "seccion_id", "orden"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(opcional=True) | models.Q(incluido=True),
                name="item_no_opcional_y_no_incluido",
            ),
        ]
        verbose_name = "Ítem de presupuesto"
        verbose_name_plural = "Ítems de presupuesto"

    def __str__(self):
        return self.descripcion_manual or str(self.producto)
