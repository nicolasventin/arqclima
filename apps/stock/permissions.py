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

    Andrés (Técnico de Campo) NO tiene manage_stock_general (decisión
    42bis, cerrada en la Etapa 9 — hasta entonces lo tenía de forma
    general, sin acotar a sus propios trabajos, porque el modelo
    Trabajo todavía no existía). Su necesidad real — enviar material a
    su trabajo y devolver el sobrante que "vuelve a stock" (regla de
    negocio 11) — está cubierta por apps.jobs.services.enviar_material()/
    registrar_sobrante(), gateadas por jobs.manage_ejecucion_propia +
    un chequeo de fila (material.trabajo.tecnico_asignado_id == user.id):
    ya acotan cada movimiento a SU trabajo, algo que esta función no
    podría expresar (no recibe ningún Trabajo como parámetro). La
    pantalla cruda de stock (esta función) quedó reservada para quien
    de verdad controla un depósito completo (Diego/Contri/Gabriel).
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



def puede_forzar_stock_negativo(user):
    """Excepción explícita para permitir una salida que deje stock negativo."""
    return user.has_perm("stock.force_negative_stock")
