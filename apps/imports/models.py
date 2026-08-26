from django.conf import settings
from django.db import models

from apps.catalog.models import Producto, Proveedor


class ImportacionListaPrecios(models.Model):
    """
    Una carga de lista de precios de un proveedor (regla de negocio 4).
    Nace en estado Pendiente con sus filas ya clasificadas pero SIN tocar
    Producto/ProductoProveedor/HistorialCosto — eso pasa recién cuando se
    confirma (apps.imports.services.confirmar_importacion).
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de confirmar"
        CONFIRMADA = "confirmada", "Confirmada"
        DESCARTADA = "descartada", "Descartada"

    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.PROTECT, related_name="importaciones"
    )
    archivo = models.FileField(upload_to="importaciones/%Y/%m/")
    cargado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="importaciones_cargadas",
    )
    cargado_en = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    confirmada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="importaciones_confirmadas",
    )
    confirmada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-cargado_en"]
        verbose_name = "Importación de lista de precios"
        verbose_name_plural = "Importaciones de listas de precios"

    def __str__(self):
        return f"Importación #{self.pk} — {self.proveedor} ({self.get_estado_display()})"


class ImportacionFila(models.Model):
    """
    Una fila del Excel ya clasificada. Guarda los valores crudos (para
    poder mostrarle al usuario exactamente qué venía en el archivo) más el
    resultado de la clasificación.
    """

    class Categoria(models.TextChoices):
        NUEVO_PRODUCTO = "nuevo_producto", "Producto nuevo"
        NUEVO_VINCULO = "nuevo_vinculo", "Nuevo vínculo con este proveedor"
        ACTUALIZA_COSTO = "actualiza_costo", "Actualiza costo"
        SIN_CAMBIOS = "sin_cambios", "Sin cambios"
        PARA_REVISAR = "para_revisar", "Para revisar"
        ERROR = "error", "Error"

    importacion = models.ForeignKey(
        ImportacionListaPrecios, on_delete=models.CASCADE, related_name="filas"
    )
    numero_fila = models.PositiveIntegerField()

    marca_texto = models.CharField(max_length=100, blank=True)
    codigo = models.CharField(max_length=100, blank=True)
    nombre_texto = models.CharField(max_length=255, blank=True)
    costo_texto = models.CharField(max_length=50, blank=True)
    costo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    codigo_proveedor_texto = models.CharField(max_length=100, blank=True)

    categoria = models.CharField(max_length=20, choices=Categoria.choices)
    detalle = models.CharField(max_length=255, blank=True)
    producto = models.ForeignKey(
        Producto, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    incluir = models.BooleanField(default=True)

    class Meta:
        ordering = ["numero_fila"]
        verbose_name = "Fila de importación"
        verbose_name_plural = "Filas de importación"

    def __str__(self):
        return f"Fila {self.numero_fila}: {self.codigo} ({self.categoria})"
