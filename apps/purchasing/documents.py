from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa


class PDFOrdenCompraError(RuntimeError):
    pass


def numero_documento_orden(orden):
    """
    Identificador humano estable del documento.

    El número secuencial de la orden ya es globalmente único. Se agrega el
    año de creación para que el PDF/correo sea más reconocible sin cambiar
    la identidad técnica de la orden.
    """
    fecha = orden.creado_en
    if timezone.is_aware(fecha):
        fecha = timezone.localtime(fecha)
    return f"OC-{fecha.year}-{orden.numero:04d}"


def _resolver_recurso(uri, rel):
    """
    xhtml2pdf necesita rutas físicas para archivos estáticos/locales.
    """
    if not uri:
        return uri

    normalizado = uri.lstrip("/")
    static_prefix = settings.STATIC_URL.lstrip("/")
    media_prefix = settings.MEDIA_URL.lstrip("/")

    if normalizado.startswith(static_prefix):
        relativo = normalizado[len(static_prefix):]
        encontrado = finders.find(relativo)
        if encontrado:
            return encontrado

    if normalizado.startswith(media_prefix):
        relativo = normalizado[len(media_prefix):]
        return str(Path(settings.MEDIA_ROOT) / relativo)

    if uri.startswith("file://"):
        return uri[7:]

    return uri


def contexto_documento_orden(orden):
    lineas = []
    total = 0
    queryset = orden.lineas.select_related(
        "producto_proveedor__producto__marca",
        "producto_proveedor__proveedor",
    ).order_by("pk")

    for linea in queryset:
        producto = linea.producto_proveedor.producto
        subtotal = linea.cantidad * linea.costo_esperado
        total += subtotal
        lineas.append(
            {
                "linea": linea,
                "producto": producto,
                "codigo": producto.codigo,
                "codigo_proveedor": linea.producto_proveedor.codigo_proveedor,
                "unidad": producto.get_unidad_medida_display(),
                "subtotal": subtotal,
            }
        )

    return {
        "orden": orden,
        "documento_numero": numero_documento_orden(orden),
        "lineas_documento": lineas,
        "total_documento": total,
        "empresa": settings.ARQCLIMA_COMPANY,
    }


def generar_pdf_orden(orden):
    html = render_to_string(
        "purchasing/orden_pdf.html",
        contexto_documento_orden(orden),
    )
    salida = BytesIO()
    resultado = pisa.CreatePDF(
        html,
        dest=salida,
        encoding="utf-8",
        link_callback=_resolver_recurso,
    )
    if resultado.err:
        raise PDFOrdenCompraError("No se pudo generar el PDF de la orden de compra.")
    return salida.getvalue()
