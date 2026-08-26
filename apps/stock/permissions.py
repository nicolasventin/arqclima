from .models import Deposito


def puede_ver_stock(user):
    return user.has_perm("stock.view_movimientostock")


def puede_registrar_entrada_salida(user, deposito):
    """
    Mismo patrón que pricing.puede_registrar_costo: un permiso genérico
    de "pase libre" (add_movimientostock, solo Diego) OR un permiso
    custom acotado por depósito para el resto de los roles. Entrada y
    salida comparten el mismo permiso por depósito — quien controla un
    depósito controla ambos movimientos.

    Andrés (Técnico de Campo) tiene manage_stock_general de forma
    GENERAL, no acotada a sus propios trabajos — es una ampliación real
    respecto de la matriz de permisos original (que solo lo listaba
    para "salida"), decidida a propósito porque la regla de negocio 11
    dice que el sobrante que retira "vuelve a stock" (una entrada), así
    que necesita las dos acciones. Cuando exista el modelo Trabajo
    (Etapa 8), evaluar si conviene acotar esto a "solo movimientos
    relacionados con sus propios trabajos asignados" en vez de dejarlo
    general para siempre.
    """
    if user.has_perm("stock.add_movimientostock"):
        return True
    if deposito == Deposito.GENERAL:
        return user.has_perm("stock.manage_stock_general")
    if deposito == Deposito.REPUESTOS:
        return user.has_perm("stock.manage_stock_repuestos")
    return False


def puede_ajustar_stock(user, deposito):
    """Ajuste manual: solo stock general (Diego, Contri) — nadie ajusta repuestos a mano."""
    if deposito == Deposito.GENERAL:
        return user.has_perm("stock.ajustar_stock_general")
    return False


def puede_configurar_stock_minimo(user):
    return user.has_perm("stock.manage_stock_minimo")
