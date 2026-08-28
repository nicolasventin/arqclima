from decimal import Decimal

from .models import ConfiguracionGeneral, HistorialCosto


def margen_efectivo(producto):
    """
    Jerarquía de márgenes: producto > marca > categoría > general.
    Gana el nivel más específico configurado; no se combinan/suman entre
    niveles. Devuelve (margen, "de dónde salió") para poder mostrarlo en
    la UI en vez de que el número parezca sacado de la nada.
    """
    if producto.margen is not None:
        return producto.margen, "producto"
    if producto.marca.margen is not None:
        return producto.marca.margen, "marca"
    if producto.categoria and producto.categoria.margen is not None:
        return producto.categoria.margen, "categoria"
    return ConfiguracionGeneral.obtener().margen_general, "general"


def costo_actual(producto_proveedor):
    """La fila más reciente de HistorialCosto para esa relación, o None."""
    return producto_proveedor.historial_costos.order_by("-vigente_desde").first()


def ultimo_costo_producto(producto):
    """
    A diferencia de costo_actual(producto_proveedor) (una relación puntual
    producto+proveedor), este helper busca el HistorialCosto más reciente
    entre TODOS los proveedores del producto — para "cuánto vale el stock"
    (Etapa 9, Parte 3: stock valorizado), donde no importa de qué
    proveedor puntual salió cada costo, solo el dato más reciente
    disponible. Devuelve None si el producto nunca tuvo un costo cargado.
    """
    return HistorialCosto.objects.filter(
        producto_proveedor__producto=producto
    ).order_by("-vigente_desde").first()


def proveedor_mas_conveniente(producto):
    """
    Entre los proveedores activos de un producto, el de menor costo actual.
    Puramente informativo (regla de negocio 2): nunca se usa para elegir
    automáticamente un proveedor en ningún flujo del sistema.

    Devuelve (ProductoProveedor, costo) o (None, None) si no hay ningún
    costo cargado todavía.
    """
    mejor = None
    mejor_costo = None
    for pp in producto.productoproveedor_set.filter(activo=True):
        historial = costo_actual(pp)
        if historial is None:
            continue
        if mejor_costo is None or historial.costo < mejor_costo:
            mejor = pp
            mejor_costo = historial.costo
    return mejor, mejor_costo


def calcular_precio_venta(producto, costo):
    """
    precio = costo x (1 + (flete% + financiero% + margen%) / 100)

    Los tres recargos se suman entre sí y se aplican una sola vez sobre el
    costo (no se componen en cadena).
    """
    config = ConfiguracionGeneral.obtener()
    margen, origen_margen = margen_efectivo(producto)
    recargo_pct = config.flete_pct + config.costo_financiero_pct + margen
    precio = costo * (Decimal("1") + recargo_pct / Decimal("100"))
    return precio.quantize(Decimal("0.01")), origen_margen


def registrar_costo(producto_proveedor, costo, usuario, origen="manual"):
    """Única forma soportada de escribir en HistorialCosto: siempre INSERT."""
    return HistorialCosto.objects.create(
        producto_proveedor=producto_proveedor,
        costo=costo,
        cargado_por=usuario,
        origen=origen,
    )
