def puede_gestionar_orden(user):
    """
    Crear, editar en Borrador, enviar a aprobación y marcar como
    enviada al proveedor — Rodrigo, Gabriel, Andrés, Diego (regla de
    negocio 7). El bloqueo real está en puede_aprobar_orden, no acá:
    "marcar enviada" no es exclusivo de Diego porque en la práctica es
    quien armó la orden el que efectivamente le escribe al proveedor.
    """
    return user.has_perm("purchasing.add_ordendecompra")


def puede_aprobar_orden(user):
    """Aprobar o rechazar — exclusivo de Diego (regla de negocio 7, bloqueo real)."""
    return user.has_perm("purchasing.approve_ordendecompra")


def puede_cancelar_orden(user):
    """Cancelar una orden ya en curso es una decisión de negocio — exclusivo de Diego."""
    return user.has_perm("purchasing.change_ordendecompra")
