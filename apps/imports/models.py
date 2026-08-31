from django.conf import settings
from django.db import models

from apps.catalog.models import Producto, Proveedor
from apps.pricing.models import Moneda


class ImportacionListaPrecios(models.Model):
    """
    Una carga de lista de precios de un proveedor.

    En 11H deja de ser exclusivamente Excel: el archivo puede ser Excel,
    CSV, PDF o Word. El parser genera filas e imágenes de apoyo, pero el
    catálogo y el historial de costos NO cambian hasta confirmar.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de confirmar"
        CONFIRMADA = "confirmada", "Confirmada"
        DESCARTADA = "descartada", "Descartada"

    class TipoArchivo(models.TextChoices):
        XLSX = "xlsx", "Excel (.xlsx)"
        XLS = "xls", "Excel antiguo (.xls)"
        XLSM = "xlsm", "Excel con macros (.xlsm)"
        CSV = "csv", "CSV"
        PDF = "pdf", "PDF"
        DOCX = "docx", "Word (.docx)"
        IMAGEN = "imagen", "Foto de lista (imagen)"

    class EstadoAnalisis(models.TextChoices):
        COMPLETO = "completo", "Analizado"
        REQUIERE_REVISION = "requiere_revision", "Requiere revisión"

    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.PROTECT, related_name="importaciones"
    )
    archivo = models.FileField(upload_to="importaciones/%Y/%m/")
    tipo_archivo = models.CharField(
        max_length=10,
        choices=TipoArchivo.choices,
        default=TipoArchivo.XLSX,
    )
    estado_analisis = models.CharField(
        max_length=30,
        choices=EstadoAnalisis.choices,
        default=EstadoAnalisis.COMPLETO,
    )
    advertencias_analisis = models.JSONField(default=list, blank=True)
    analizado_en = models.DateTimeField(null=True, blank=True)

    usa_ia = models.BooleanField(
        default=False,
        help_text="Se usó Claude (mapeo de columnas y/o extracción) para analizar esta importación.",
    )
    ia_resultado = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Metadatos de las llamadas a Claude para esta importación (modelo, "
            "tokens, filas devueltas por origen). No guarda el archivo ni la "
            "API key; solo la respuesta estructurada ya parseada, para poder "
            "auditar una extracción dudosa sin volver a llamar a la API."
        ),
    )

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
    Una fila detectada por el analizador.

    Guarda texto crudo, origen y confianza además del resultado de la
    clasificación. PDF/Word pueden necesitar corrección manual antes de
    poder confirmarse; una fila PARA_REVISAR nunca se aplica directamente.
    """

    class Categoria(models.TextChoices):
        NUEVO_PRODUCTO = "nuevo_producto", "Producto nuevo"
        NUEVO_VINCULO = "nuevo_vinculo", "Nuevo vínculo con este proveedor"
        ACTUALIZA_COSTO = "actualiza_costo", "Actualiza costo"
        SIN_CAMBIOS = "sin_cambios", "Sin cambios"
        PARA_REVISAR = "para_revisar", "Para revisar"
        ERROR = "error", "Error"

    class Confianza(models.TextChoices):
        ALTA = "alta", "Alta"
        MEDIA = "media", "Media"
        BAJA = "baja", "Baja"
        REVISADA = "revisada", "Revisada por usuario"

    importacion = models.ForeignKey(
        ImportacionListaPrecios, on_delete=models.CASCADE, related_name="filas"
    )
    numero_fila = models.PositiveIntegerField()
    origen = models.CharField(max_length=150, blank=True)
    confianza = models.CharField(
        max_length=20,
        choices=Confianza.choices,
        default=Confianza.ALTA,
    )

    marca_texto = models.CharField(max_length=100, blank=True)
    codigo = models.CharField(max_length=100, blank=True)
    nombre_texto = models.CharField(max_length=255, blank=True)
    descripcion_texto = models.TextField(blank=True)
    costo_texto = models.CharField(max_length=100, blank=True)
    costo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    moneda = models.CharField(max_length=3, choices=Moneda.choices, default=Moneda.ARS)
    codigo_proveedor_texto = models.CharField(max_length=100, blank=True)
    unidad_texto = models.CharField(max_length=50, blank=True)
    categoria_texto = models.CharField(max_length=100, blank=True)

    categoria = models.CharField(max_length=20, choices=Categoria.choices)
    detalle = models.CharField(max_length=500, blank=True)
    producto = models.ForeignKey(
        Producto, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    incluir = models.BooleanField(default=True)

    class Meta:
        ordering = ["origen", "numero_fila", "pk"]
        verbose_name = "Fila de importación"
        verbose_name_plural = "Filas de importación"

    def __str__(self):
        return f"{self.origen or 'Archivo'} · fila {self.numero_fila}: {self.codigo} ({self.categoria})"


class ImportacionImagen(models.Model):
    """
    Imagen embebida extraída de Excel/Word/PDF.

    En 11H funciona como evidencia visual de la lista del proveedor. No se
    asigna automáticamente a Producto porque el catálogo todavía no tiene
    un campo de imagen y, sobre todo, porque una imagen sola no identifica
    de forma segura un producto.
    """

    importacion = models.ForeignKey(
        ImportacionListaPrecios,
        on_delete=models.CASCADE,
        related_name="imagenes",
    )
    archivo = models.ImageField(upload_to="importaciones/imagenes/%Y/%m/")
    origen = models.CharField(max_length=150, blank=True)
    numero_fila_origen = models.PositiveIntegerField(null=True, blank=True)
    nombre_original = models.CharField(max_length=255, blank=True)
    ancho = models.PositiveIntegerField(null=True, blank=True)
    alto = models.PositiveIntegerField(null=True, blank=True)
    huella_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ["origen", "numero_fila_origen", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["importacion", "huella_sha256"],
                name="imports_imagen_unica_por_importacion",
            ),
        ]
        verbose_name = "Imagen de importación"
        verbose_name_plural = "Imágenes de importación"

    def __str__(self):
        return f"Imagen de importación #{self.importacion_id} · {self.origen or 'archivo'}"


class ProveedorColumnMapping(models.Model):
    """
    Cachea el mapeo de columnas que Claude Haiku resuelve para una hoja
    Excel/CSV "plana" cuyos encabezados no matchean los alias conocidos
    (apps.imports.parsing.CAMPOS_ALIAS).

    La clave es (proveedor, hash de encabezados), no solo proveedor: un
    mismo proveedor puede mandar listas con layouts distintos con el tiempo,
    y cada layout nuevo genera su propia fila en vez de pisar la anterior.
    Mientras el header no cambie, nunca se vuelve a llamar a la API para
    ese proveedor.
    """

    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.CASCADE, related_name="mapeos_columnas_ia"
    )
    encabezados = models.JSONField(
        help_text="Encabezados crudos tal como se leyeron del archivo, para poder auditar a qué corresponde el hash."
    )
    encabezados_hash = models.CharField(max_length=64)
    mapeo = models.JSONField(
        help_text="Campo canónico → índice de columna (0-based), igual forma que detectar_columnas()."
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["proveedor", "encabezados_hash"],
                name="imports_mapeo_columnas_unico_por_proveedor_hash",
            ),
        ]
        verbose_name = "Mapeo de columnas (IA)"
        verbose_name_plural = "Mapeos de columnas (IA)"

    def __str__(self):
        return f"Mapeo IA · {self.proveedor} · {self.encabezados_hash[:8]}"
