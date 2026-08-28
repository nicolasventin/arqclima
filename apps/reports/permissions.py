def puede_ver_reporte_comercial(user):
    return user.has_perm("reports.view_reporte_comercial")


def puede_ver_montos_confidenciales(user):
    """
    Regla explícita de Diego: nadie más que Administrador ve montos
    agregados de facturación/ingresos. Un solo permiso compartido por
    todos los reportes que muestren montos (Comercial, Rentabilidad,
    Stock valorizado) — es la misma regla de negocio en todos los
    casos, no una por pantalla.
    """
    return user.has_perm("reports.view_montos_confidenciales")


def puede_ver_reporte_rentabilidad(user):
    return user.has_perm("reports.view_reporte_rentabilidad")


def puede_ver_reporte_stock(user):
    return user.has_perm("reports.view_reporte_stock")
