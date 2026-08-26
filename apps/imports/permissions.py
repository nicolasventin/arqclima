from apps.pricing.permissions import puede_registrar_costo


def puede_importar(user):
    """
    No hay un permiso propio de "importar": reutiliza directamente
    puede_registrar_costo (importar es, ni más ni menos, cargar costos en
    lote). Diego entra por pricing.add_historialcosto; Gabriel entra
    escopeado por pricing.manage_costos_repuestos — cada fila se valida
    igual, individualmente, al categorizar y de nuevo al confirmar.
    """
    return puede_registrar_costo(user, producto=None)
