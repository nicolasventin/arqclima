def puede_revertir_aceptado(user):
    """
    Revertir un presupuesto Aceptado (volverlo a Cancelado) es una
    decisión de negocio, no un trámite de rutina del ciclo diario de
    Rodrigo: un Aceptado es la puerta de entrada a que nazca un Trabajo
    (Etapa 8). Solo Administrador tiene 'revert_presupuesto_aceptado'.
    """
    return user.has_perm("quotes.revert_presupuesto_aceptado")
