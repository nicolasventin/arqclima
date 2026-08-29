def puede_gestionar_orden(user):
    """
    Crear, editar en Borrador, emitir y enviar al proveedor.

    En 11K desaparece el permiso especial de aprobación. Por ahora estas
    acciones siguen usando add_ordendecompra como permiso operativo base;
    la Etapa 11M revisará la matriz fina por rol.
    """
    return user.has_perm("purchasing.add_ordendecompra")


def puede_cancelar_orden(user):
    """Cancelar una orden ya en curso es una decisión de negocio — exclusivo de Dirección."""
    return user.has_perm("purchasing.change_ordendecompra")


def puede_cerrar_orden(user):
    """Cerrar una recepción parcial/completa es decisión de negocio — exclusivo de Dirección."""
    return user.has_perm("purchasing.change_ordendecompra")
