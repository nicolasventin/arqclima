from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
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


def _lineas_texto(texto):
    return [
        linea.strip().lstrip("-").lstrip("✓").lstrip("✔").strip()
        for linea in (texto or "").splitlines()
        if linea.strip()
    ]


def _overlay_encabezado(presupuesto, ancho, alto):
    """Genera un encabezado ReportLab para no depender del soporte de imágenes de xhtml2pdf."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(ancho, alto))

    margen_x = 42.52  # 15 mm
    margen_superior = 11.34  # 4 mm
    linea_y = alto - 65.20  # ~23 mm desde arriba

    logo = finders.find("img/arqclima-logo-pdf.png")
    if logo:
        ancho_logo = 113.39  # 40 mm
        alto_logo = ancho_logo * 191 / 420
        pdf.drawImage(
            logo,
            margen_x,
            alto - margen_superior - alto_logo,
            width=ancho_logo,
            height=alto_logo,
            preserveAspectRatio=True,
            mask="auto",
        )
    else:
        pdf.setFillColor(HexColor("#075b9b"))
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(margen_x, alto - 31, "ARQCLIMA")

    empresa = settings.ARQCLIMA_COMPANY
    x_derecha = ancho - margen_x
    y = alto - 20

    pdf.setFillColor(HexColor("#075b9b"))
    pdf.setFont("Helvetica-Bold", 7.3)
    pdf.drawRightString(x_derecha, y, f"Presupuesto Nº {presupuesto.numero}")

    pdf.setFillColor(HexColor("#547080"))
    pdf.setFont("Helvetica", 6.3)
    y -= 8
    pdf.drawRightString(x_derecha, y, presupuesto.fecha.strftime("%d/%m/%Y"))

    for dato in (
        empresa.get("direccion"),
        f"Tel/Wapp {empresa.get('telefono')}" if empresa.get("telefono") else "",
        empresa.get("email"),
    ):
        if dato:
            y -= 7
            pdf.drawRightString(x_derecha, y, str(dato))

    pdf.setStrokeColor(HexColor("#d7e1e7"))
    pdf.setLineWidth(0.7)
    pdf.line(margen_x, linea_y, ancho - margen_x, linea_y)

    pdf.save()
    buffer.seek(0)
    return buffer


def _aplicar_encabezado(pdf_base, presupuesto):
    lector = PdfReader(pdf_base)
    escritor = PdfWriter()

    for pagina in lector.pages:
        ancho = float(pagina.mediabox.width)
        alto = float(pagina.mediabox.height)
        overlay = _overlay_encabezado(presupuesto, ancho, alto)
        pagina_overlay = PdfReader(overlay).pages[0]
        pagina.merge_page(pagina_overlay)
        escritor.add_page(pagina)

    salida = BytesIO()
    escritor.write(salida)
    salida.seek(0)
    return salida


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

    base = BytesIO()
    resultado = pisa.CreatePDF(
        html,
        dest=base,
        encoding="utf-8",
        link_callback=_resolver_recurso,
    )
    if resultado.err:
        raise PDFPresupuestoError("No se pudo generar el PDF del presupuesto.")

    base.seek(0)
    final = _aplicar_encabezado(base, presupuesto)
    dest.write(final.getvalue())
    return resultado
