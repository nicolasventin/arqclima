def puede_ver_precio(user, producto=None):
    """
    Mismo patrón que catalog.permissions.puede_editar_producto: rol amplio
    (Diego, Rodrigo) ve todo; rol escopeado (Gabriel) solo ve precios de
    productos de su línea de repuestos.
    """
    if user.has_perm("pricing.view_historialcosto"):
        return True
    if user.has_perm("pricing.view_precio_repuestos"):
        return producto is None or producto.es_repuesto
    return False


def puede_registrar_costo(user, producto=None):
    if user.has_perm("pricing.add_historialcosto"):
        return True
    if user.has_perm("pricing.manage_costos_repuestos"):
        return producto is None or producto.es_repuesto
    return False


def puede_gestionar_margenes(user):
    return user.has_perm("pricing.manage_margenes")
