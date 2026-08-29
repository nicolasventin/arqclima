from django.db import models


class Marca(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    activa = models.BooleanField(default=True)
    margen = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Margen (%) para todos los productos de esta marca. Vacío = usar el general.",
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(margen__isnull=True) | models.Q(margen__gte=0),
                name="marca_margen_no_negativo",
            ),
        ]
        verbose_name = "Marca"
        verbose_name_plural = "Marcas"

    def __str__(self):
        return self.nombre


class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    margen = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Margen (%) para productos de esta categoría. Vacío = usar el general.",
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(margen__isnull=True) | models.Q(margen__gte=0),
                name="categoria_margen_no_negativo",
            ),
        ]
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre_comercial = models.CharField(max_length=150)
    razon_social = models.CharField(max_length=150, blank=True)
    cuit = models.CharField(max_length=20, blank=True)
    contacto_nombre = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notas = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre_comercial"]
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"

    def __str__(self):
        return self.nombre_comercial


class UnidadMedida(models.TextChoices):
    UNIDAD = "unidad", "Unidad"
    METRO = "metro", "Metro"
    METRO_CUADRADO = "m2", "Metro cuadrado"
    KILOGRAMO = "kg", "Kilogramo"
    LITRO = "litro", "Litro"
    CAJA = "caja", "Caja"
    ROLLO = "rollo", "Rollo"
    PAR = "par", "Par"
    KIT = "kit", "Kit"


class Producto(models.Model):
    """
    La identidad real de un producto es (marca, codigo) — el código oficial
    del fabricante, nunca uno inventado por ARQCLIMA (regla de negocio 1).
    El PK numérico es solo un detalle técnico interno.

    `es_repuesto` no es excluyente: un producto puede aparecer en el
    catálogo general Y en la línea de repuestos de Gabriel a la vez.
    """

    marca = models.ForeignKey(Marca, on_delete=models.PROTECT, related_name="productos")
    codigo = models.CharField(
        max_length=100,
        help_text="Código oficial del fabricante/marca. No se inventa un código propio de ARQCLIMA.",
    )
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True)
    categoria = models.ForeignKey(
        Categoria, on_delete=models.SET_NULL, null=True, blank=True, related_name="productos"
    )
    unidad_medida = models.CharField(
        max_length=20, choices=UnidadMedida.choices, default=UnidadMedida.UNIDAD
    )
    es_repuesto = models.BooleanField(
        default=False,
        help_text="También forma parte de la línea de repuestos de service.",
    )
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    margen = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text=(
            "Margen (%) propio de este producto. Vacío = usar el de su marca, "
            "categoría o el general (en ese orden). Se edita aparte del resto "
            "del producto: es exclusivo de Diego, aunque Gabriel pueda editar "
            "el resto de los datos de un producto de su línea."
        ),
    )
    stock_minimo_general = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=(
            "Umbral de alerta de stock general (obra). Vacío = sin alerta "
            "configurada. Exclusivo de Diego (Etapa 7)."
        ),
    )
    stock_minimo_repuestos = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=(
            "Umbral de alerta de stock de repuestos (service). Vacío = sin "
            "alerta configurada. Exclusivo de Diego (Etapa 7)."
        ),
    )

    proveedores = models.ManyToManyField(
        Proveedor, through="ProductoProveedor", related_name="productos"
    )

    class Meta:
        ordering = ["marca__nombre", "codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["marca", "codigo"], name="producto_unico_por_marca_codigo"
            ),
            models.CheckConstraint(
                check=models.Q(margen__isnull=True) | models.Q(margen__gte=0),
                name="producto_margen_no_negativo",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(stock_minimo_general__isnull=True)
                    | models.Q(stock_minimo_general__gte=0)
                ),
                name="producto_stock_min_general_no_negativo",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(stock_minimo_repuestos__isnull=True)
                    | models.Q(stock_minimo_repuestos__gte=0)
                ),
                name="producto_stock_min_repuestos_no_negativo",
            ),
        ]
        permissions = [
            ("manage_repuestos", "Puede crear y editar productos de la línea de repuestos"),
        ]
        verbose_name = "Producto"
        verbose_name_plural = "Productos"

    def __str__(self):
        return f"{self.marca.nombre} {self.codigo} — {self.nombre}"


class ProductoProveedor(models.Model):
    """
    Relación producto↔proveedor (regla de negocio 2: multi-proveedor).
    Todavía sin costo: el historial de costos se cuelga de acá en la
    Etapa 3, sin tener que tocar este modelo.
    """

    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="productoproveedor_set"
    )
    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.CASCADE, related_name="productoproveedor_set"
    )
    codigo_proveedor = models.CharField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["producto", "proveedor"], name="producto_proveedor_unico"
            ),
        ]
        verbose_name = "Producto de proveedor"
        verbose_name_plural = "Productos por proveedor"

    def __str__(self):
        return f"{self.producto} — {self.proveedor}"
