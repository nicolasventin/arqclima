from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action

from .documents import generar_pdf_orden, numero_documento_orden
from .models import EstadoEnvioOrdenCompra, EstadoOrdenCompra, OrdenDeCompra
from .permissions import puede_gestionar_orden


class EnvioOrdenCompraError(RuntimeError):
    pass


def _mensaje_email(orden, documento_numero):
    proveedor = orden.proveedor
    saludo = proveedor.contacto_nombre.strip() if proveedor.contacto_nombre else proveedor.nombre_comercial
    return (
        f"Hola {saludo},\n\n"
        f"Adjuntamos la orden de compra {documento_numero} de ARQCLIMA.\n"
        "Por favor, confirmar recepción, disponibilidad y cualquier observación necesaria.\n\n"
        "Saludos,\n"
        "ARQCLIMA"
    )


@transaction.atomic
def _reservar_envio(orden, usuario):
    if not puede_gestionar_orden(usuario):
        raise PermissionError("No tiene permiso para enviar órdenes de compra.")

    orden_bloqueada = (
        OrdenDeCompra.objects.select_for_update()
        .select_related("proveedor")
        .get(pk=orden.pk)
    )

    if orden_bloqueada.estado != EstadoOrdenCompra.EMITIDA:
        raise EnvioOrdenCompraError("Solo se puede enviar por correo una orden Emitida.")

    if not orden_bloqueada.lineas.exists():
        raise EnvioOrdenCompraError("No se puede enviar una orden sin líneas.")

    if not orden_bloqueada.proveedor.email:
        raise EnvioOrdenCompraError(
            "El proveedor no tiene un email cargado. Completalo antes de enviar la orden."
        )

    if orden_bloqueada.estado_envio == EstadoEnvioOrdenCompra.ENVIANDO:
        raise EnvioOrdenCompraError(
            "Ya hay un envío en curso para esta orden. No vuelvas a enviarla hasta que termine."
        )

    if orden_bloqueada.estado_envio == EstadoEnvioOrdenCompra.ENVIADO:
        raise EnvioOrdenCompraError("Esta orden ya figura como enviada.")

    orden_bloqueada.estado_envio = EstadoEnvioOrdenCompra.ENVIANDO
    orden_bloqueada.ultimo_intento_envio_en = timezone.now()
    orden_bloqueada.ultimo_error_envio = ""
    orden_bloqueada.save(
        update_fields=[
            "estado_envio",
            "ultimo_intento_envio_en",
            "ultimo_error_envio",
        ]
    )

    log_action(
        usuario,
        "iniciar_envio_orden_compra",
        orden_bloqueada,
        detail=f"Inicio de envío a {orden_bloqueada.proveedor.email}.",
    )
    return orden_bloqueada


def _obtener_pdf(orden):
    if orden.pdf_generado:
        try:
            with orden.pdf_generado.open("rb") as archivo:
                contenido = archivo.read()
            if contenido:
                return contenido
        except OSError:
            # Si el archivo desapareció del storage, se regenera porque la
            # orden está congelada mientras permanezca Emitida.
            pass

    contenido = generar_pdf_orden(orden)
    nombre = f"{numero_documento_orden(orden)}.pdf"
    orden.pdf_generado.save(nombre, ContentFile(contenido), save=False)
    orden.save(update_fields=["pdf_generado"])
    return contenido


def _registrar_error_envio(orden_id, usuario, mensaje):
    mensaje_limpio = (mensaje or "Error desconocido")[:2000]
    with transaction.atomic():
        orden = OrdenDeCompra.objects.select_for_update().get(pk=orden_id)
        if (
            orden.estado == EstadoOrdenCompra.EMITIDA
            and orden.estado_envio == EstadoEnvioOrdenCompra.ENVIANDO
        ):
            orden.estado_envio = EstadoEnvioOrdenCompra.ERROR
            orden.ultimo_error_envio = mensaje_limpio
            orden.save(update_fields=["estado_envio", "ultimo_error_envio"])
            log_action(
                usuario,
                "error_envio_orden_compra",
                orden,
                detail=mensaje_limpio,
            )


def _confirmar_envio(orden_id, usuario, destinatario, documento_numero):
    with transaction.atomic():
        orden = OrdenDeCompra.objects.select_for_update().get(pk=orden_id)
        if orden.estado != EstadoOrdenCompra.EMITIDA:
            raise EnvioOrdenCompraError(
                "El correo fue enviado, pero la orden cambió de estado antes de poder registrarlo. "
                "No la reenvíes hasta revisar el caso."
            )
        if orden.estado_envio != EstadoEnvioOrdenCompra.ENVIANDO:
            raise EnvioOrdenCompraError(
                "El correo fue enviado, pero el registro técnico del envío cambió inesperadamente. "
                "No la reenvíes hasta revisar el caso."
            )

        ahora = timezone.now()
        orden.estado = EstadoOrdenCompra.ENVIADA
        orden.estado_envio = EstadoEnvioOrdenCompra.ENVIADO
        orden.enviada_por = usuario
        orden.enviada_en = ahora
        orden.enviada_a = destinatario
        orden.ultimo_error_envio = ""
        orden.save(
            update_fields=[
                "estado",
                "estado_envio",
                "enviada_por",
                "enviada_en",
                "enviada_a",
                "ultimo_error_envio",
            ]
        )

        log_action(
            usuario,
            "enviar_orden_compra_proveedor",
            orden,
            detail=f"Email enviado a {destinatario}. Documento {documento_numero}.",
        )
        return orden


def enviar_orden_por_email(orden, usuario):
    """
    Genera/conserva el PDF oficial, lo adjunta al correo y recién después
    marca la orden como Enviada.

    El estado técnico ENVIANDO evita dobles clics/envíos concurrentes. Si
    falla PDF o SMTP, la orden permanece Emitida y queda en ERROR para poder
    reintentar sin perder trazabilidad.
    """
    reservada = _reservar_envio(orden, usuario)
    destinatario = reservada.proveedor.email
    documento_numero = numero_documento_orden(reservada)

    try:
        pdf = _obtener_pdf(reservada)
        asunto = f"Orden de compra {documento_numero} — ARQCLIMA"
        mensaje = EmailMessage(
            subject=asunto,
            body=_mensaje_email(reservada, documento_numero),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario],
            reply_to=(
                [settings.ARQCLIMA_COMPANY["email"]]
                if settings.ARQCLIMA_COMPANY.get("email")
                else None
            ),
        )
        mensaje.attach(
            f"{documento_numero}.pdf",
            pdf,
            "application/pdf",
        )
        enviados = mensaje.send(fail_silently=False)
        if enviados != 1:
            raise RuntimeError("El backend de correo no confirmó el envío.")
    except Exception as exc:
        _registrar_error_envio(reservada.pk, usuario, str(exc))
        raise EnvioOrdenCompraError(
            "No se pudo enviar la orden por correo. Revisá la configuración de email "
            "o intentá nuevamente."
        ) from exc

    try:
        confirmada = _confirmar_envio(
            reservada.pk,
            usuario,
            destinatario,
            documento_numero,
        )
    except Exception as exc:
        raise EnvioOrdenCompraError(
            "El correo pudo haber sido enviado, pero no se pudo confirmar el registro final. "
            "No lo reintentes hasta verificar si el proveedor lo recibió."
        ) from exc

    orden.estado = confirmada.estado
    orden.estado_envio = confirmada.estado_envio
    orden.enviada_por = confirmada.enviada_por
    orden.enviada_en = confirmada.enviada_en
    orden.enviada_a = confirmada.enviada_a
    orden.pdf_generado = confirmada.pdf_generado
    return confirmada
