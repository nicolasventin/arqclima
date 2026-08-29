import csv
import hashlib
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

import openpyxl
import xlrd
from docx import Document
from PIL import Image, ImageOps
from pypdf import PdfReader


MAX_ARCHIVO_BYTES = 30 * 1024 * 1024
MAX_FILAS = 10_000
MAX_PAGINAS_PDF = 250
MAX_IMAGENES = 60
MAX_IMAGEN_BYTES = 8 * 1024 * 1024
MAX_IMAGEN_PIXELES = 24_000_000
MAX_ZIP_DESCOMPRIMIDO = 180 * 1024 * 1024
MAX_RATIO_ZIP = 250

ALIAS_MARCA = {"marca", "brand", "fabricante", "manufacturer"}
ALIAS_CODIGO = {
    "codigo", "código", "code", "sku", "modelo", "model", "referencia", "ref",
    "cod", "cod.", "cod. fabricante", "cod fabricante", "codigo fabricante",
    "código fabricante", "part number", "part no", "pn",
}
ALIAS_NOMBRE = {
    "nombre", "descripcion", "descripción", "producto", "detalle", "articulo",
    "artículo", "item", "ítem", "concepto",
}
ALIAS_COSTO = {
    "costo", "precio", "precio neto", "precio unitario", "importe",
    "precio de costo", "neto", "precio lista", "precio de lista", "price",
    "unit price", "p. unitario",
}
ALIAS_CODIGO_PROVEEDOR = {
    "codigo proveedor", "código proveedor", "cod. proveedor", "cod proveedor",
    "sku proveedor", "codigo interno", "código interno", "cod interno",
}
ALIAS_UNIDAD = {
    "unidad", "unidad de medida", "u.m.", "um", "unit", "medida",
}
ALIAS_CATEGORIA = {
    "categoria", "categoría", "familia", "rubro", "linea", "línea", "grupo",
}
ALIAS_DESCRIPCION_EXTENDIDA = {
    "descripcion extendida", "descripción extendida", "descripcion tecnica",
    "descripción técnica", "caracteristicas", "características", "observaciones",
}

CAMPOS_REQUERIDOS = ("codigo", "nombre", "costo")
CAMPOS_ALIAS = {
    "marca": ALIAS_MARCA,
    "codigo": ALIAS_CODIGO,
    "nombre": ALIAS_NOMBRE,
    "costo": ALIAS_COSTO,
    "codigo_proveedor": ALIAS_CODIGO_PROVEEDOR,
    "unidad": ALIAS_UNIDAD,
    "categoria": ALIAS_CATEGORIA,
    "descripcion": ALIAS_DESCRIPCION_EXTENDIDA,
}


@dataclass
class FilaAnalizada:
    numero: int
    origen: str
    datos: dict
    confianza: str = "alta"


@dataclass
class ImagenAnalizada:
    contenido: bytes
    extension: str
    origen: str
    nombre_original: str = ""
    numero_fila_origen: int | None = None
    ancho: int | None = None
    alto: int | None = None
    huella_sha256: str = ""


@dataclass
class ResultadoAnalisis:
    filas: list[FilaAnalizada] = field(default_factory=list)
    imagenes: list[ImagenAnalizada] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)


def _normalizar(texto):
    texto = str(texto or "").strip().lower()
    texto = "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto


_ALIAS_NORMALIZADOS = {
    campo: {_normalizar(alias) for alias in aliases}
    for campo, aliases in CAMPOS_ALIAS.items()
}


class ColumnasNoDetectadas(Exception):
    def __init__(self, faltantes, encabezados, origen=""):
        self.faltantes = faltantes
        self.encabezados = [str(h) for h in encabezados if str(h or "").strip()]
        prefijo = f"{origen}: " if origen else ""
        mensaje = (
            f"{prefijo}No se pudieron reconocer las columnas: {', '.join(faltantes)}. "
            f"Encabezados encontrados: {', '.join(self.encabezados) or '(ninguno)'}."
        )
        super().__init__(mensaje)


class ArchivoImportacionInvalido(ValueError):
    pass


def tipo_archivo_por_nombre(nombre):
    extension = Path(nombre or "").suffix.lower().lstrip(".")
    if extension not in {"xlsx", "xls", "csv", "pdf", "docx"}:
        raise ArchivoImportacionInvalido(
            "Formato no soportado. Usá .xlsx, .xls, .csv, .pdf o .docx."
        )
    return extension


def detectar_columnas(encabezados):
    """
    Detecta columnas por aliases. Código, nombre y costo son obligatorios.

    Marca dejó de ser obligatoria en 11H: algunos proveedores identifican
    inequívocamente por su propio código y otros mandan listas de una sola
    marca. Si falta, la fila se conserva para revisión en vez de descartar
    todo el archivo.
    """
    normalizados = [_normalizar(h) for h in encabezados]
    mapeo = {}
    faltantes = []

    for campo, aliases in _ALIAS_NORMALIZADOS.items():
        indice = next(
            (i for i, valor in enumerate(normalizados) if valor in aliases),
            None,
        )
        if indice is not None:
            mapeo[campo] = indice
        elif campo in CAMPOS_REQUERIDOS:
            faltantes.append(campo)

    if faltantes:
        raise ColumnasNoDetectadas(faltantes, encabezados)
    return mapeo


def _puntaje_encabezado(encabezados):
    normalizados = [_normalizar(h) for h in encabezados]
    return sum(
        1
        for aliases in _ALIAS_NORMALIZADOS.values()
        if any(valor in aliases for valor in normalizados)
    )


def _detectar_encabezado(matriz, origen, limite=25):
    mejor = (0, [])
    for posicion, (_, fila) in enumerate(matriz[:limite]):
        puntaje = _puntaje_encabezado(fila)
        if puntaje > mejor[0]:
            mejor = (puntaje, fila)
        try:
            return posicion, detectar_columnas(fila)
        except ColumnasNoDetectadas:
            continue
    faltantes = list(CAMPOS_REQUERIDOS)
    raise ColumnasNoDetectadas(faltantes, mejor[1], origen=origen)


def parsear_costo(valor):
    """
    Tolera números, separadores argentinos/internacionales y texto con
    símbolos de moneda. Devuelve None si no se puede interpretar.
    """
    if valor is None or valor == "":
        return None

    if isinstance(valor, (int, float, Decimal)):
        try:
            return Decimal(str(valor)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    texto = str(valor).replace("\xa0", " ").strip()
    if not texto:
        return None

    coincidencia = re.search(r"-?\(?\d[\d.,\s]*\)?", texto)
    if not coincidencia:
        return None

    numero = coincidencia.group(0).strip().replace(" ", "")
    negativo_parentesis = numero.startswith("(") and numero.endswith(")")
    numero = numero.strip("()")

    if "," in numero and "." in numero:
        if numero.rfind(",") > numero.rfind("."):
            numero = numero.replace(".", "").replace(",", ".")
        else:
            numero = numero.replace(",", "")
    elif "," in numero:
        numero = numero.replace(".", "").replace(",", ".")

    try:
        valor_decimal = Decimal(numero)
        if negativo_parentesis:
            valor_decimal = -valor_decimal
        return valor_decimal.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _texto_celda(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _extraer_filas_matriz(matriz, origen, confianza="alta"):
    if not matriz:
        return [], []

    posicion_header, mapeo = _detectar_encabezado(matriz, origen)
    advertencias = []
    if "marca" not in mapeo:
        advertencias.append(
            f"{origen}: no se detectó columna Marca. Las filas nuevas deberán "
            "tener una marca resoluble antes de confirmarse."
        )

    filas = []
    for numero, valores in matriz[posicion_header + 1 :]:
        if len(filas) >= MAX_FILAS:
            advertencias.append(
                f"{origen}: se alcanzó el límite de {MAX_FILAS} filas; el resto no se analizó."
            )
            break
        if not valores or all(not _texto_celda(v) for v in valores):
            continue

        # Evita repetir un encabezado cuando una lista imprime cabecera en cada página.
        try:
            detectar_columnas(valores)
        except ColumnasNoDetectadas:
            pass
        else:
            continue

        def obtener(campo):
            indice = mapeo.get(campo)
            if indice is None or indice >= len(valores):
                return ""
            return valores[indice]

        costo_crudo = obtener("costo")
        filas.append(
            FilaAnalizada(
                numero=max(1, int(numero)),
                origen=origen,
                confianza=confianza,
                datos={
                    "marca": _texto_celda(obtener("marca")),
                    "codigo": _texto_celda(obtener("codigo")),
                    "nombre": _texto_celda(obtener("nombre")),
                    "descripcion": _texto_celda(obtener("descripcion")),
                    "costo_crudo": costo_crudo,
                    "codigo_proveedor": _texto_celda(obtener("codigo_proveedor")),
                    "unidad": _texto_celda(obtener("unidad")),
                    "categoria": _texto_celda(obtener("categoria")),
                },
            )
        )
    return filas, advertencias


def _validar_zip_seguro(datos):
    try:
        with zipfile.ZipFile(BytesIO(datos)) as zf:
            total = 0
            for info in zf.infolist():
                total += info.file_size
                if total > MAX_ZIP_DESCOMPRIMIDO:
                    raise ArchivoImportacionInvalido(
                        "El archivo comprimido expande demasiado contenido y fue rechazado por seguridad."
                    )
                if info.compress_size > 0 and info.file_size / info.compress_size > MAX_RATIO_ZIP:
                    raise ArchivoImportacionInvalido(
                        "El archivo tiene una relación de compresión anormal y fue rechazado por seguridad."
                    )
    except zipfile.BadZipFile as exc:
        raise ArchivoImportacionInvalido("El archivo está dañado o no es un documento válido.") from exc


def _normalizar_imagen(contenido, origen, nombre="", numero_fila=None):
    if not contenido or len(contenido) > MAX_IMAGEN_BYTES:
        return None, "Se omitió una imagen demasiado grande."

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            original = Image.open(BytesIO(contenido))
            ancho, alto = original.size
            if ancho * alto > MAX_IMAGEN_PIXELES:
                return None, "Se omitió una imagen con resolución excesiva."
            imagen = ImageOps.exif_transpose(original)
            imagen.thumbnail((1600, 1600))

            salida = BytesIO()
            tiene_alpha = "A" in imagen.getbands()
            if tiene_alpha:
                imagen.save(salida, format="PNG", optimize=True)
                extension = "png"
            else:
                if imagen.mode != "RGB":
                    imagen = imagen.convert("RGB")
                imagen.save(salida, format="JPEG", quality=86, optimize=True)
                extension = "jpg"
            data = salida.getvalue()
    except (
        Image.UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ):
        return None, "Se omitió una imagen embebida que no pudo validarse."

    return (
        ImagenAnalizada(
            contenido=data,
            extension=extension,
            origen=origen,
            nombre_original=nombre,
            numero_fila_origen=numero_fila,
            ancho=imagen.width,
            alto=imagen.height,
            huella_sha256=hashlib.sha256(data).hexdigest(),
        ),
        None,
    )


def _agregar_imagen(resultado, contenido, origen, nombre="", numero_fila=None):
    if len(resultado.imagenes) >= MAX_IMAGENES:
        if not any("límite de imágenes" in a for a in resultado.advertencias):
            resultado.advertencias.append(
                f"Se alcanzó el límite de imágenes ({MAX_IMAGENES}); las restantes se omitieron."
            )
        return

    imagen, advertencia = _normalizar_imagen(
        contenido,
        origen=origen,
        nombre=nombre,
        numero_fila=numero_fila,
    )
    if advertencia:
        resultado.advertencias.append(f"{origen}: {advertencia}")
        return
    if imagen is None:
        return
    if any(existing.huella_sha256 == imagen.huella_sha256 for existing in resultado.imagenes):
        return
    resultado.imagenes.append(imagen)


def _analizar_xlsx(datos):
    _validar_zip_seguro(datos)
    resultado = ResultadoAnalisis()

    libro = openpyxl.load_workbook(BytesIO(datos), data_only=True, read_only=True)
    try:
        for hoja in libro.worksheets:
            matriz = []
            for numero, fila in enumerate(hoja.iter_rows(values_only=True), start=1):
                matriz.append((numero, list(fila)))
                if numero >= MAX_FILAS + 30:
                    break
            if not any(any(_texto_celda(v) for v in valores) for _, valores in matriz):
                continue
            origen = f"Hoja {hoja.title}"
            try:
                filas, adv = _extraer_filas_matriz(matriz, origen, confianza="alta")
                resultado.filas.extend(filas)
                resultado.advertencias.extend(adv)
            except ColumnasNoDetectadas as exc:
                resultado.advertencias.append(str(exc))
    finally:
        libro.close()

    # openpyxl no expone imágenes en read_only; se abre una segunda vez.
    try:
        libro_img = openpyxl.load_workbook(BytesIO(datos), data_only=True, read_only=False)
        try:
            for hoja in libro_img.worksheets:
                for imagen in getattr(hoja, "_images", []):
                    anchor = getattr(imagen, "anchor", None)
                    desde = getattr(anchor, "_from", None)
                    numero_fila = getattr(desde, "row", None)
                    if numero_fila is not None:
                        numero_fila += 1
                    try:
                        contenido = imagen._data()
                    except Exception:
                        resultado.advertencias.append(
                            f"Hoja {hoja.title}: no se pudo extraer una imagen embebida."
                        )
                        continue
                    _agregar_imagen(
                        resultado,
                        contenido,
                        origen=f"Hoja {hoja.title}",
                        nombre=getattr(imagen, "path", "") or "",
                        numero_fila=numero_fila,
                    )
        finally:
            libro_img.close()
    except Exception:
        resultado.advertencias.append(
            "El contenido tabular del Excel se analizó, pero no se pudieron inspeccionar sus imágenes."
        )

    return resultado


def _analizar_xls(datos):
    resultado = ResultadoAnalisis()
    try:
        libro = xlrd.open_workbook(file_contents=datos, on_demand=True)
    except xlrd.XLRDError as exc:
        raise ArchivoImportacionInvalido("El archivo .xls está dañado o no es válido.") from exc

    try:
        for hoja in libro.sheets():
            matriz = [
                (numero + 1, hoja.row_values(numero))
                for numero in range(min(hoja.nrows, MAX_FILAS + 30))
            ]
            if not matriz:
                continue
            origen = f"Hoja {hoja.name}"
            try:
                filas, adv = _extraer_filas_matriz(matriz, origen, confianza="alta")
                resultado.filas.extend(filas)
                resultado.advertencias.extend(adv)
            except ColumnasNoDetectadas as exc:
                resultado.advertencias.append(str(exc))
    finally:
        libro.release_resources()

    resultado.advertencias.append(
        "El formato .xls antiguo no permite extraer imágenes de forma confiable; se analizó solo la tabla."
    )
    return resultado


def _decodificar_csv(datos):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return datos.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ArchivoImportacionInvalido("No se pudo interpretar la codificación del CSV.")


def _analizar_csv(datos):
    resultado = ResultadoAnalisis()
    texto, encoding = _decodificar_csv(datos)
    muestra = texto[:8192]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
        dialecto.delimiter = ";"

    lector = csv.reader(texto.splitlines(), dialect=dialecto)
    matriz = []
    for numero, fila in enumerate(lector, start=1):
        if numero > MAX_FILAS + 30:
            resultado.advertencias.append(
                f"CSV: se alcanzó el límite de {MAX_FILAS} filas."
            )
            break
        matriz.append((numero, fila))

    filas, adv = _extraer_filas_matriz(matriz, "CSV", confianza="alta")
    resultado.filas.extend(filas)
    resultado.advertencias.extend(adv)
    if encoding not in ("utf-8-sig", "utf-8"):
        resultado.advertencias.append(
            f"CSV: se interpretó usando codificación {encoding}."
        )
    return resultado


def _analizar_docx(datos):
    _validar_zip_seguro(datos)
    resultado = ResultadoAnalisis()
    try:
        documento = Document(BytesIO(datos))
    except Exception as exc:
        raise ArchivoImportacionInvalido("El Word está dañado o no es un .docx válido.") from exc

    tablas_detectadas = 0
    for indice, tabla in enumerate(documento.tables, start=1):
        matriz = [
            (numero, [celda.text.strip() for celda in fila.cells])
            for numero, fila in enumerate(tabla.rows, start=1)
        ]
        if not matriz:
            continue
        origen = f"Tabla {indice} de Word"
        try:
            filas, adv = _extraer_filas_matriz(matriz, origen, confianza="alta")
        except ColumnasNoDetectadas as exc:
            resultado.advertencias.append(str(exc))
            continue
        tablas_detectadas += 1
        resultado.filas.extend(filas)
        resultado.advertencias.extend(adv)

    # Algunos Word contienen una lista tabulada en párrafos en vez de tabla.
    if tablas_detectadas == 0:
        matriz = []
        for numero, parrafo in enumerate(documento.paragraphs, start=1):
            texto = parrafo.text.strip()
            if not texto:
                continue
            celdas = [p.strip() for p in re.split(r"\t+|\s{2,}", texto) if p.strip()]
            if len(celdas) >= 2:
                matriz.append((numero, celdas))
        if matriz:
            try:
                filas, adv = _extraer_filas_matriz(
                    matriz,
                    "Párrafos tabulados de Word",
                    confianza="media",
                )
                resultado.filas.extend(filas)
                resultado.advertencias.extend(adv)
            except ColumnasNoDetectadas as exc:
                resultado.advertencias.append(str(exc))

    # Extrae imágenes desde las relaciones del paquete DOCX.
    for rel in documento.part.rels.values():
        if "image" not in rel.reltype:
            continue
        try:
            blob = rel.target_part.blob
            nombre = Path(str(rel.target_ref)).name
        except Exception:
            continue
        _agregar_imagen(
            resultado,
            blob,
            origen="Documento Word",
            nombre=nombre,
        )

    return resultado


def _lineas_pdf_a_matriz(texto):
    matriz = []
    for numero, linea in enumerate((texto or "").splitlines(), start=1):
        limpia = linea.strip()
        if not limpia:
            continue
        celdas = [
            parte.strip()
            for parte in re.split(r"\t+|\s{2,}", limpia)
            if parte.strip()
        ]
        if len(celdas) >= 2:
            matriz.append((numero, celdas))
    return matriz


def _analizar_pdf(datos):
    resultado = ResultadoAnalisis()
    try:
        reader = PdfReader(BytesIO(datos), strict=False)
    except Exception as exc:
        raise ArchivoImportacionInvalido("El PDF está dañado o no se puede leer.") from exc

    if len(reader.pages) > MAX_PAGINAS_PDF:
        raise ArchivoImportacionInvalido(
            f"El PDF tiene {len(reader.pages)} páginas; el máximo permitido es {MAX_PAGINAS_PDF}."
        )

    paginas_con_texto = 0
    paginas_con_tabla = 0

    for numero_pagina, pagina in enumerate(reader.pages, start=1):
        try:
            texto = pagina.extract_text(extraction_mode="layout") or ""
        except Exception:
            try:
                texto = pagina.extract_text() or ""
            except Exception:
                texto = ""

        if texto.strip():
            paginas_con_texto += 1
            matriz = _lineas_pdf_a_matriz(texto)
            if matriz:
                origen = f"Página {numero_pagina} del PDF"
                try:
                    filas, adv = _extraer_filas_matriz(
                        matriz,
                        origen,
                        confianza="media",
                    )
                    if filas:
                        paginas_con_tabla += 1
                    resultado.filas.extend(filas)
                    resultado.advertencias.extend(adv)
                except ColumnasNoDetectadas as exc:
                    resultado.advertencias.append(str(exc))
        else:
            resultado.advertencias.append(
                f"Página {numero_pagina} del PDF: no contiene texto extraíble. "
                "Puede ser una página escaneada; se preservan sus imágenes, pero 11H "
                "no hace OCR automático para evitar interpretar precios erróneos."
            )

        try:
            imagenes_pagina = list(pagina.images)
        except Exception:
            imagenes_pagina = []
        for imagen in imagenes_pagina:
            try:
                contenido = imagen.data
                nombre = getattr(imagen, "name", "") or ""
            except Exception:
                continue
            _agregar_imagen(
                resultado,
                contenido,
                origen=f"Página {numero_pagina} del PDF",
                nombre=nombre,
            )

    if paginas_con_texto == 0:
        resultado.advertencias.append(
            "El PDF no tiene texto extraíble. Se conservaron las imágenes detectables "
            "para revisión, pero no se generaron productos automáticamente."
        )
    elif paginas_con_tabla == 0:
        resultado.advertencias.append(
            "Se pudo leer texto del PDF, pero no se detectó una tabla con Código, "
            "Nombre/Descripción y Costo/Precio."
        )

    return resultado


def analizar_archivo(archivo, tipo_archivo=None):
    """
    Punto único de análisis 11H. Lee como máximo 30 MB y despacha por formato.

    Devuelve filas + imágenes + advertencias. Ningún parser toca catálogo,
    proveedores ni precios.
    """
    if tipo_archivo is None:
        tipo_archivo = tipo_archivo_por_nombre(getattr(archivo, "name", ""))

    try:
        archivo.open("rb")
    except AttributeError:
        pass
    try:
        if hasattr(archivo, "seek"):
            archivo.seek(0)
        datos = archivo.read(MAX_ARCHIVO_BYTES + 1)
    finally:
        try:
            archivo.close()
        except Exception:
            pass

    if len(datos) > MAX_ARCHIVO_BYTES:
        raise ArchivoImportacionInvalido(
            f"El archivo supera el máximo de {MAX_ARCHIVO_BYTES // (1024 * 1024)} MB."
        )

    parsers = {
        "xlsx": _analizar_xlsx,
        "xls": _analizar_xls,
        "csv": _analizar_csv,
        "pdf": _analizar_pdf,
        "docx": _analizar_docx,
    }
    parser = parsers.get(tipo_archivo)
    if parser is None:
        raise ArchivoImportacionInvalido("Formato de archivo no soportado.")

    resultado = parser(datos)
    if len(resultado.filas) > MAX_FILAS:
        resultado.filas = resultado.filas[:MAX_FILAS]
        resultado.advertencias.append(
            f"Se recortó el análisis al máximo de {MAX_FILAS} filas."
        )
    return resultado


def leer_excel(archivo):
    """
    Compatibilidad con la API de Etapa 4. Solo devuelve filas del primer
    análisis XLSX; el código nuevo usa analizar_archivo().
    """
    resultado = analizar_archivo(archivo, "xlsx")
    if not resultado.filas:
        raise ColumnasNoDetectadas(
            list(CAMPOS_REQUERIDOS),
            [],
            origen="Excel",
        )
    for fila in resultado.filas:
        yield fila.numero, {
            "marca": fila.datos["marca"],
            "codigo": fila.datos["codigo"],
            "nombre": fila.datos["nombre"],
            "costo_crudo": fila.datos["costo_crudo"],
            "codigo_proveedor": fila.datos["codigo_proveedor"],
        }
