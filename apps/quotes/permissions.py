def puede_revertir_aceptado(user, presupuesto):
    """
    Revertir un presupuesto Aceptado (volverlo a Cancelado) es una
    decisión de negocio, no un trámite de rutina del ciclo diario de
    Rodrigo. Solo Administrador tiene 'revert_presupuesto_aceptado'.

    Etapa 8: si el presupuesto ya tiene un Trabajo creado, ni siquiera
    Diego puede revertirlo desde acá — el Trabajo ya está en curso (o
    recién nace) a partir de este presupuesto, y dejarlo volver a
    Cancelado por atrás dejaría al Trabajo apuntando a un presupuesto
    inconsistente. Si de verdad hay que dar de baja el trabajo, se
    resuelve a nivel del Trabajo primero, no revirtiendo el presupuesto
    por afuera.
    """
    if hasattr(presupuesto, "trabajo"):
        return False
    return user.has_perm("quotes.revert_presupuesto_aceptado")
