from apps.jobs.models import EstadoTrabajo


def puede_revertir_aceptado(user, presupuesto):
    """
    Revertir un presupuesto Aceptado (volverlo a Cancelado) es una
    decisión de negocio, no un trámite de rutina del ciclo diario de
    Rodrigo. Solo Administrador tiene 'revert_presupuesto_aceptado'.

    Etapa 8: si el presupuesto ya tiene un Trabajo creado y ACTIVO
    (cualquier estado salvo Cancelado), ni siquiera Diego puede
    revertirlo desde acá — el Trabajo está en curso a partir de este
    presupuesto, y dejarlo volver a Cancelado por atrás lo dejaría
    apuntando a un presupuesto inconsistente. Si el Trabajo mismo ya
    está Cancelado (la obra se cayó), no hay ninguna razón para seguir
    bloqueando la reversión — al contrario, tiene sentido poder
    deshacer también la aceptación del presupuesto que le dio origen.
    """
    if hasattr(presupuesto, "trabajo") and presupuesto.trabajo.estado != EstadoTrabajo.CANCELADO:
        return False
    return user.has_perm("quotes.revert_presupuesto_aceptado")
