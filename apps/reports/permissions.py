def puede_ver_reporte_comercial(user):
    return user.has_perm("reports.view_reporte_comercial")


def puede_ver_montos_confidenciales(user):
    """
    Regla explícita de Diego: nadie más que Administrador ve montos
    agregados de facturación/ingresos. Un solo permiso compartido por
    todos los reportes que muestren montos (Comercial, Rentabilidad en
    la Parte 3) — es la misma regla de negocio en todos los casos, no
    una por pantalla.
    """
    return user.has_perm("reports.view_montos_confidenciales")
