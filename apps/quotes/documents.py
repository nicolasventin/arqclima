import base64
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from xhtml2pdf import pisa

from .services import calcular_totales


class PDFPresupuestoError(RuntimeError):
    pass


def _resolver_recurso(uri, rel):
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




def _logo_pdf_data_uri():
    ruta = finders.find("img/arqclima-logo-pdf.png")
    if not ruta:
        return ""
    contenido = Path(ruta).read_bytes()
    codificado = base64.b64encode(contenido).decode("ascii")
    return f"data:image/png;base64,{codificado}"


def _lineas_texto(texto):
    return [
        linea.strip().lstrip("-").lstrip("✓").lstrip("✔").strip()
        for linea in (texto or "").splitlines()
        if linea.strip()
    ]


def contexto_pdf_presupuesto(presupuesto):
    lineas_comerciales = list(
        presupuesto.lineas_comerciales.select_related("seccion").order_by(
            "seccion__orden",
            "orden",
            "pk",
        )
    )
    hay_lineas_comerciales = bool(lineas_comerciales)

    secciones = []
    for seccion in presupuesto.secciones.all().order_by("orden", "pk"):
        lineas = [linea for linea in lineas_comerciales if linea.seccion_id == seccion.pk]
        secciones.append(
            {
                "seccion": seccion,
                "descripcion_lineas": _lineas_texto(seccion.descripcion_publica),
                "lineas": [linea for linea in lineas if not linea.opcional],
                "opcionales": [linea for linea in lineas if linea.opcional],
                "items_publicos": list(
                    seccion.items.filter(incluido=True).select_related("producto", "producto__marca")
                )
                if not hay_lineas_comerciales
                else [],
            }
        )

    return {
        "presupuesto": presupuesto,
        "empresa": settings.ARQCLIMA_COMPANY,
        "logo_data_uri": _logo_pdf_data_uri(),
        "totales": calcular_totales(presupuesto),
        "hay_lineas_comerciales": hay_lineas_comerciales,
        "secciones": secciones,
        "lineas_sin_seccion": [
            linea
            for linea in lineas_comerciales
            if linea.seccion_id is None and not linea.opcional
        ],
        "opcionales_sin_seccion": [
            linea
            for linea in lineas_comerciales
            if linea.seccion_id is None and linea.opcional
        ],
        "items_sin_seccion": (
            presupuesto.items.filter(seccion__isnull=True, incluido=True).select_related(
                "producto",
                "producto__marca",
            )
            if not hay_lineas_comerciales
            else []
        ),
        "alcance_lineas": _lineas_texto(presupuesto.alcance_tecnico),
        "notas_lineas": _lineas_texto(presupuesto.notas_cliente),
        "forma_pago_lineas": _lineas_texto(presupuesto.forma_pago),
        "exclusiones_lineas": _lineas_texto(presupuesto.exclusiones),
    }


def generar_pdf_presupuesto(presupuesto, dest):
    html = render_to_string(
        "quotes/presupuesto_pdf.html",
        contexto_pdf_presupuesto(presupuesto),
    )
    resultado = pisa.CreatePDF(
        html,
        dest=dest,
        encoding="utf-8",
        link_callback=_resolver_recurso,
    )
    if resultado.err:
        raise PDFPresupuestoError("No se pudo generar el PDF del presupuesto.")
    return resultado
