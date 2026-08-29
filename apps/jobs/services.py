from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import log_action
from apps.catalog.models import Producto
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.stock.models import Deposito, TipoMovimiento
from apps.stock.services import registrar_movimiento

from .models import ORDEN_ESTADOS, EstadoTrabajo, EtapaTrabajo, MaterialTrabajo, Trabajo
from .permissions import puede_cancelar_trabajo, puede_finalizar_trabajo


class TransicionInvalidaError(ValueError):
    pass


@transaction.atomic
def crear_trabajo(presupuesto, usuario, tecnico_asignado=None):
    """
    Único punto de entrada para que nazca un Trabajo. No es un efecto
    automático de aceptar el presupuesto — alguien con permiso dispara
    esta acción a propósito, después de que el presupuesto ya está
    Aceptado.

    Se bloquea el Presupuesto porque es la identidad única de origen
    del Trabajo. Dos requests simultáneos se serializan sobre esa fila
    y el segundo vuelve a comprobar si el Trabajo ya existe.
    """
    presupuesto_bloqueado = (
        Presupuesto.objects.select_for_update()
        .select_related("cliente")
        .get(pk=presupuesto.pk)
    )
    if presupuesto_bloqueado.estado != EstadoPresupuesto.ACEPTADO:
        raise ValueError("Solo se puede crear un Trabajo a partir de un Presupuesto Aceptado.")
    if Trabajo.objects.filter(presupuesto=presupuesto_bloqueado).exists():
        raise ValueError("Este presupuesto ya tiene un Trabajo creado.")

    trabajo = Trabajo.objects.create(
        presupuesto=presupuesto_bloqueado,
        tecnico_asignado=tecnico_asignado,
        direccion=presupuesto_bloqueado.direccion,
        observaciones=presupuesto_bloqueado.notas_generales,
        creado_por=usuario,
    )
    log_action(
        usuario,
        "crear_trabajo",
        trabajo,
        detail=f"Trabajo creado desde {presupuesto_bloqueado}",
    )
    return trabajo


@transaction.atomic
def cambiar_estado_trabajo(trabajo, nuevo_estado, usuario, detalle=""):
    """
    El estado de un Trabajo normalmente avanza —y se puede saltear
    etapas (ej. Pendiente→Listo directo)— pero TAMBIÉN se puede
    retroceder para corregir un error de carga (ej. Contri marcó
    "Listo" sin que estuviera completo el material, o Andrés avanzó a
    "En ejecución" antes de tiempo). Mismo criterio que Presupuesto,
    que también tiene transiciones explícitas de vuelta atrás
    (Enviado→Borrador, Rechazado→Borrador): no hay razón de negocio
    para que Trabajo sea estrictamente irreversible, y menos acá,
    donde presupuesto.trabajo es OneToOne — si un estado avanzado por
    error quedara sin forma de corregirse, no habría ni siquiera el
    workaround de "crear uno nuevo" que tiene Tarea.

    Esta función NO valida permisos — es la misma separación de
    responsabilidades que Presupuesto.cambiar_estado(): quién puede
    pedir qué transición (en cualquier dirección) se resuelve en
    apps.jobs.permissions.puede_cambiar_estado_trabajo(), llamado
    desde la vista.
    """
    trabajo_bloqueado = (
        Trabajo.objects.select_for_update()
        .select_related("presupuesto__cliente")
        .get(pk=trabajo.pk)
    )

    if trabajo_bloqueado.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise TransicionInvalidaError(
            f"El trabajo está '{trabajo_bloqueado.estado}' y ya está cerrado."
        )
    if nuevo_estado == EstadoTrabajo.TERMINADO:
        raise TransicionInvalidaError(
            "Terminado es un cierre operativo: use finalizar_trabajo()."
        )

    try:
        idx_actual = ORDEN_ESTADOS.index(trabajo_bloqueado.estado)
    except ValueError:
        raise TransicionInvalidaError(
            f"El trabajo está '{trabajo_bloqueado.estado}': no forma parte de la secuencia de avance "
            "(un Cancelado no se reabre)."
        )
    try:
        idx_nuevo = ORDEN_ESTADOS.index(nuevo_estado)
    except ValueError:
        raise TransicionInvalidaError(f"Estado desconocido: '{nuevo_estado}'.")

    if idx_nuevo == idx_actual:
        raise TransicionInvalidaError(
            f"El trabajo ya está en estado '{trabajo_bloqueado.estado}'."
        )

    estado_anterior = trabajo_bloqueado.estado
    trabajo_bloqueado.estado = nuevo_estado
    trabajo_bloqueado.save(update_fields=["estado"])

    accion = "cambiar_estado_trabajo"
    direccion = "avanzado" if idx_nuevo > idx_actual else "retrocedido"

    # Pasar a Listo con material sin enviar sigue siendo una advertencia
    # operativa y no un bloqueo: el bloqueo fuerte aparece al FINALIZAR
    # el trabajo en 10F.
    # Regla de negocio 6/Etapa 7 aplicada acá: pasar a Listo con
    # material sin enviar NO bloquea (Contri puede tener razones para
    # marcarlo igual), pero queda auditado con el detalle de qué
    # faltaba en ESE momento — mismo criterio que el margen bajo en
    # enviar_presupuesto(). No se sobreescribe si el caller ya mandó un
    # detalle propio.
    if nuevo_estado == EstadoTrabajo.LISTO and not detalle:
        pendientes = materiales_pendientes_de_envio(trabajo_bloqueado)
        if pendientes:
            accion = "trabajo_marcado_listo_con_pendientes"
            detalle = "Marcado Listo con material pendiente de envío: " + "; ".join(
                f"{m} (faltan {cantidad_pendiente_envio(m)})" for m in pendientes
            )

    log_action(
        usuario,
        accion,
        trabajo_bloqueado,
        detail=detalle or f"{direccion}: {estado_anterior} → {nuevo_estado}",
    )
    trabajo.estado = nuevo_estado
    return trabajo_bloqueado


@transaction.atomic
def cancelar_trabajo(trabajo, usuario, motivo=""):
    """
    Cancelado es una salida terminal APARTE de ORDEN_ESTADOS (no
    "avanza" ni "retrocede" — ver el comentario en models.py). Se
    permite desde cualquier estado no resuelto: un trabajo Terminado
    ya está resuelto, y uno ya Cancelado no se cancela de nuevo. No
    hay reapertura: si el trabajo cancelado necesita retomarse, es una
    decisión nueva de negocio, no una transición de estado.
    """
    trabajo_bloqueado = (
        Trabajo.objects.select_for_update()
        .select_related("presupuesto__cliente")
        .get(pk=trabajo.pk)
    )
    if not puede_cancelar_trabajo(usuario):
        raise PermissionError("No tiene permiso para cancelar trabajos.")
    if trabajo_bloqueado.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise TransicionInvalidaError(
            f"No se puede cancelar un trabajo en estado '{trabajo_bloqueado.estado}'."
        )

    motivo_limpio = (motivo or "").strip()
    if not motivo_limpio:
        raise ValueError("Debe indicar el motivo de la cancelación.")

    estado_anterior = trabajo_bloqueado.estado
    trabajo_bloqueado.estado = EstadoTrabajo.CANCELADO
    trabajo_bloqueado.cancelado_por = usuario
    trabajo_bloqueado.cancelado_en = timezone.now()
    trabajo_bloqueado.motivo_cancelacion = motivo_limpio
    trabajo_bloqueado.save(
        update_fields=[
            "estado",
            "cancelado_por",
            "cancelado_en",
            "motivo_cancelacion",
        ]
    )
    log_action(
        usuario,
        "cancelar_trabajo",
        trabajo_bloqueado,
        detail=f"Cancelado desde '{estado_anterior}'. Motivo: {motivo_limpio}",
    )
    trabajo.estado = EstadoTrabajo.CANCELADO
    trabajo.cancelado_por = usuario
    trabajo.cancelado_en = trabajo_bloqueado.cancelado_en
    trabajo.motivo_cancelacion = motivo_limpio
    return trabajo_bloqueado


def motivos_bloqueo_finalizacion(trabajo):
    """
    Devuelve razones humanas por las que un trabajo todavía no se puede
    cerrar como Terminado. No muta nada; la validación autoritativa se
    repite bajo locks dentro de finalizar_trabajo().
    """
    motivos = []
    if trabajo.estado != EstadoTrabajo.EN_EJECUCION:
        motivos.append("El trabajo debe estar En ejecución antes de finalizarlo.")
    if trabajo.tecnico_asignado_id is None:
        motivos.append("El trabajo debe tener un técnico asignado.")

    pendientes = materiales_pendientes_de_envio(trabajo)
    if pendientes:
        detalle = "; ".join(
            f"{material} (faltan {cantidad_pendiente_envio(material)})"
            for material in pendientes
        )
        motivos.append(f"Quedan materiales de catálogo sin enviar: {detalle}.")
    return motivos


@transaction.atomic
def finalizar_trabajo(trabajo, usuario, observaciones=""):
    """
    Cierre operativo real de 10F.

    Solo se finaliza desde En ejecución, con técnico asignado y sin
    materiales de catálogo pendientes de envío. Se bloquean Trabajo y
    sus MaterialTrabajo antes de recalcular pendientes, evitando que una
    edición/envío concurrente cambie la foto durante el cierre.
    """
    trabajo_bloqueado = (
        Trabajo.objects.select_for_update()
        .select_related("presupuesto__cliente", "tecnico_asignado")
        .get(pk=trabajo.pk)
    )
    if not puede_finalizar_trabajo(usuario, trabajo_bloqueado):
        raise PermissionError("No tiene permiso para finalizar este trabajo.")
    if trabajo_bloqueado.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise TransicionInvalidaError("El trabajo ya está cerrado.")
    if trabajo_bloqueado.estado != EstadoTrabajo.EN_EJECUCION:
        raise TransicionInvalidaError(
            "Solo se puede finalizar un trabajo que esté En ejecución."
        )
    if trabajo_bloqueado.tecnico_asignado_id is None:
        raise ValueError("No se puede finalizar un trabajo sin técnico asignado.")

    list(
        MaterialTrabajo.objects.select_for_update()
        .filter(trabajo_id=trabajo_bloqueado.pk)
        .order_by("pk")
    )
    pendientes = materiales_pendientes_de_envio(trabajo_bloqueado)
    if pendientes:
        detalle = "; ".join(
            f"{material} (faltan {cantidad_pendiente_envio(material)})"
            for material in pendientes
        )
        raise ValueError(
            "No se puede finalizar: quedan materiales de catálogo sin enviar: "
            + detalle
        )

    ahora = timezone.now()
    observaciones_limpias = (observaciones or "").strip()
    trabajo_bloqueado.estado = EstadoTrabajo.TERMINADO
    trabajo_bloqueado.terminado_por = usuario
    trabajo_bloqueado.terminado_en = ahora
    trabajo_bloqueado.observaciones_cierre = observaciones_limpias
    trabajo_bloqueado.save(
        update_fields=[
            "estado",
            "terminado_por",
            "terminado_en",
            "observaciones_cierre",
        ]
    )
    log_action(
        usuario,
        "finalizar_trabajo",
        trabajo_bloqueado,
        detail=(
            f"Trabajo finalizado. Técnico: {trabajo_bloqueado.tecnico_asignado}. "
            f"Observaciones: {observaciones_limpias or 'sin observaciones'}"
        ),
    )

    trabajo.estado = EstadoTrabajo.TERMINADO
    trabajo.terminado_por = usuario
    trabajo.terminado_en = ahora
    trabajo.observaciones_cierre = observaciones_limpias
    return trabajo_bloqueado


@transaction.atomic
def generar_listado_materiales(trabajo, usuario):
    """
    Acción explícita y única (no automática al crear el trabajo, mismo
    criterio que crear_trabajo() en sí) — no es una resincronización:
    bloquea si el trabajo ya tiene materiales o etapas cargadas. A
    partir de la carga inicial, el listado se edita a mano.

    Crea una EtapaTrabajo por cada SeccionPresupuesto del presupuesto
    de origen (en orden), y un MaterialTrabajo por cada ItemPresupuesto
    con producto de catálogo e incluido=True (los conceptos manuales
    tipo mano de obra no son "material" y se excluyen).

    ItemPresupuesto.cantidad es la cantidad para UNA unidad (regla de
    negocio 9: "cantidad de unidades" multiplica a nivel de todo el
    presupuesto, no ítem por ítem — mismo criterio que calcular_totales()
    en quotes/services.py). Un presupuesto de "3 casas" con 2 termostatos
    por casa necesita 6 termostatos en total para preparar la obra, no 2
    — por eso acá SÍ hay que multiplicar por cantidad_unidades, a
    diferencia de ItemPresupuesto donde el campo se deja sin tocar y el
    factor se aplica solo una vez al calcular el total en dinero.
    """
    trabajo_bloqueado = (
        Trabajo.objects.select_for_update()
        .select_related("presupuesto")
        .get(pk=trabajo.pk)
    )
    if trabajo_bloqueado.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise ValueError("No se puede modificar materiales de un trabajo cerrado.")
    if trabajo_bloqueado.materiales.exists() or trabajo_bloqueado.etapas.exists():
        raise ValueError("Este trabajo ya tiene un listado de materiales generado.")

    mapa_etapas = {
        seccion.pk: EtapaTrabajo.objects.create(
            trabajo=trabajo_bloqueado,
            titulo=seccion.titulo,
            seccion_origen=seccion,
            orden=seccion.orden,
        )
        for seccion in trabajo_bloqueado.presupuesto.secciones.all()
    }

    cantidad_unidades = trabajo_bloqueado.presupuesto.cantidad_unidades
    items = trabajo_bloqueado.presupuesto.items.filter(producto__isnull=False, incluido=True)
    for item in items:
        MaterialTrabajo.objects.create(
            trabajo=trabajo_bloqueado,
            etapa=mapa_etapas.get(item.seccion_id),
            producto=item.producto,
            item_presupuesto_origen=item,
            cantidad_necesaria=item.cantidad * cantidad_unidades,
            orden=item.orden,
        )

    log_action(
        usuario, "generar_listado_materiales", trabajo_bloqueado,
        detail=(
            f"{items.count()} material(es) generados desde "
            f"{trabajo_bloqueado.presupuesto}"
        ),
    )
    return trabajo_bloqueado


# --- Parte 3: envío y consumo real de materiales (regla de negocio 11) ---
#
# Nada de esto se persiste como campo mutable en MaterialTrabajo — se
# deriva sumando el ledger de MovimientoStock vinculado a cada línea
# (mismo criterio que stock_actual() y cantidad_pendiente_devolucion()
# en la Etapa 7). Los materiales SIN producto de catálogo (texto
# libre) no tienen conexión con Stock: quedan en 0 para todo esto.


def cantidad_enviada(material):
    if material.producto_id is None:
        return Decimal("0")
    total = material.movimientos_stock.filter(tipo=TipoMovimiento.SALIDA).aggregate(
        total=Sum("cantidad")
    )["total"] or Decimal("0")
    return abs(total)


def cantidad_devuelta(material):
    if material.producto_id is None:
        return Decimal("0")
    return material.movimientos_stock.filter(tipo=TipoMovimiento.ENTRADA).aggregate(
        total=Sum("cantidad")
    )["total"] or Decimal("0")


def cantidad_pendiente_envio(material):
    """Cuánto falta enviar del plan (cantidad_necesaria) — 0 si ya se mandó todo o si es manual."""
    if material.producto_id is None:
        return Decimal("0")
    return material.cantidad_necesaria - cantidad_enviada(material)


def cantidad_usada_neta(material):
    """Lo enviado menos lo que ya volvió como sobrante — lo que se asume efectivamente consumido."""
    return cantidad_enviada(material) - cantidad_devuelta(material)


def materiales_pendientes_de_envio(trabajo):
    return [
        material
        for material in trabajo.materiales.filter(producto__isnull=False)
        if cantidad_pendiente_envio(material) > 0
    ]


@transaction.atomic
def enviar_material(
    material,
    usuario,
    *,
    forzar_stock_negativo=False,
    motivo_forzado="",
):
    """
    Regla de negocio 11: "se asume que se usó todo" — manda exactamente
    lo que falta del plan (cantidad_necesaria menos lo ya enviado
    antes), no pide una cantidad. Si cantidad_necesaria se edita hacia
    arriba después de un envío, un nuevo envío manda solo el delta.
    """
    # Bloquear solo MaterialTrabajo. producto es nullable y un
    # select_related() produciría un LEFT OUTER JOIN sobre el que
    # PostgreSQL no permite FOR UPDATE.
    material_bloqueado = MaterialTrabajo.objects.select_for_update().get(pk=material.pk)
    if material_bloqueado.trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise ValueError("No se puede enviar material de un trabajo cerrado.")
    if material_bloqueado.producto_id is None:
        raise ValueError("Este material no tiene producto de catálogo — no se puede enviar desde Stock.")

    pendiente = cantidad_pendiente_envio(material_bloqueado)
    if pendiente <= 0:
        raise ValueError("Este material ya fue enviado por completo.")

    return registrar_movimiento(
        producto=material_bloqueado.producto,
        deposito=Deposito.GENERAL,
        tipo=TipoMovimiento.SALIDA,
        cantidad=-pendiente,
        usuario=usuario,
        trabajo=material_bloqueado.trabajo,
        material_trabajo=material_bloqueado,
        referencia_libre=f"Envío a {material_bloqueado.trabajo}",
        forzar_stock_negativo=forzar_stock_negativo,
        motivo_forzado=motivo_forzado,
    )


@transaction.atomic
def enviar_materiales_pendientes(
    trabajo,
    usuario,
    *,
    forzar_stock_negativo=False,
    motivo_forzado="",
):
    """
    Envía en bloque todos los materiales de catálogo con algo pendiente.

    Se bloquean todas las líneas en orden de PK antes de calcular
    pendientes. Así el lote completo es atómico y el orden estable de
    locks evita deadlocks entre dos envíos masivos simultáneos.
    """
    trabajo_actual = Trabajo.objects.select_for_update().get(pk=trabajo.pk)
    if trabajo_actual.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise ValueError("No se puede enviar material de un trabajo cerrado.")

    materiales = list(
        MaterialTrabajo.objects.select_for_update()
        .filter(trabajo_id=trabajo.pk, producto__isnull=False)
        .order_by("pk")
    )

    # 10D agrega un lock de Producto dentro de registrar_movimiento().
    # Para el envío masivo tomamos primero todos los productos en orden
    # estable de PK: dos trabajos distintos que comparten productos no
    # pueden invertir el orden de locks y caer en deadlock.
    producto_ids = sorted({material.producto_id for material in materiales})
    list(
        Producto.objects.select_for_update()
        .filter(pk__in=producto_ids)
        .order_by("pk")
    )

    movimientos = []
    for material in materiales:
        pendiente = cantidad_pendiente_envio(material)
        if pendiente <= 0:
            continue
        movimientos.append(
            registrar_movimiento(
                producto=material.producto,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.SALIDA,
                cantidad=-pendiente,
                usuario=usuario,
                trabajo=material.trabajo,
                material_trabajo=material,
                referencia_libre=f"Envío a {material.trabajo}",
                forzar_stock_negativo=forzar_stock_negativo,
                motivo_forzado=motivo_forzado,
            )
        )
    return movimientos


@transaction.atomic
def registrar_sobrante(material, cantidad_sobrante, usuario):
    """
    Regla de negocio 11: el sobrante vuelve a stock. Es una ENTRADA
    simple a stock general — no se reusa el tipo Devolución, que en la
    Etapa 7 tiene un significado específico y distinto (atado a
    requiere_devolucion/salida_relacionada, exclusivo del circuito de
    repuestos de Gabriel, que no pasa por Trabajo).
    """
    if cantidad_sobrante <= 0:
        raise ValueError("La cantidad de sobrante tiene que ser mayor a cero.")

    # Igual que en enviar_material(): FOR UPDATE se aplica solamente a
    # la fila MaterialTrabajo; sus relaciones se leen después.
    material_bloqueado = MaterialTrabajo.objects.select_for_update().get(pk=material.pk)
    if material_bloqueado.trabajo.estado in (EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO):
        raise ValueError("No se puede registrar sobrante de un trabajo cerrado.")
    if material_bloqueado.producto_id is None:
        raise ValueError("Este material no tiene producto de catálogo — no se puede devolver a Stock.")

    maximo = cantidad_usada_neta(material_bloqueado)
    if cantidad_sobrante > maximo:
        raise ValueError(f"No puede superar lo enviado y no devuelto todavía ({maximo}).")

    return registrar_movimiento(
        producto=material_bloqueado.producto,
        deposito=Deposito.GENERAL,
        tipo=TipoMovimiento.ENTRADA,
        cantidad=cantidad_sobrante,
        usuario=usuario,
        trabajo=material_bloqueado.trabajo,
        material_trabajo=material_bloqueado,
        referencia_libre=f"Sobrante devuelto de {material_bloqueado.trabajo}",
    )
