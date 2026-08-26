import unicodedata
from decimal import Decimal, InvalidOperation

import openpyxl

# Alias de encabezados que reconocemos (normalizados: minúsculas, sin
# acentos). Si el Excel de un proveedor no usa ninguno de estos nombres
# para una columna requerida, se rechaza el archivo con un mensaje claro
# en vez de adivinar. Ver ColumnasNoDetectadas.
ALIAS_MARCA = {"marca", "brand", "fabricante"}
ALIAS_CODIGO = {
    "codigo", "código", "code", "sku",
    "cod. fabricante", "cod fabricante", "codigo fabricante", "código fabricante",
}
ALIAS_NOMBRE = {"nombre", "descripcion", "descripción", "producto", "detalle", "articulo", "artículo"}
ALIAS_COSTO = {"costo", "precio", "precio neto", "precio unitario", "importe", "precio de costo"}
ALIAS_CODIGO_PROVEEDOR = {
    "codigo proveedor", "código proveedor", "cod. proveedor", "cod proveedor", "sku proveedor",
}


def _normalizar(texto):
    texto = (texto or "").strip().lower()
    texto = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return texto


class ColumnasNoDetectadas(Exception):
    def __init__(self, faltantes, encabezados):
        self.faltantes = faltantes
        self.encabezados = [h for h in encabezados if h]
        mensaje = (
            f"No se pudieron reconocer las columnas: {', '.join(faltantes)}. "
            f"Encabezados encontrados en el archivo: {', '.join(self.encabezados) or '(ninguno)'}."
        )
        super().__init__(mensaje)


def detectar_columnas(encabezados):
    """
    Devuelve {campo: indice} a partir de la fila de encabezados, matcheando
    contra alias conocidos. Lanza ColumnasNoDetectadas si falta alguna
    columna requerida (marca, codigo, nombre, costo). codigo_proveedor es
    opcional.
    """
    normalizados = [_normalizar(h) for h in encabezados]
    mapeo = {}
    faltantes = []

    for campo, alias in (
        ("marca", ALIAS_MARCA),
        ("codigo", ALIAS_CODIGO),
        ("nombre", ALIAS_NOMBRE),
        ("costo", ALIAS_COSTO),
    ):
        indice = next((i for i, h in enumerate(normalizados) if h in alias), None)
        if indice is None:
            faltantes.append(campo)
        else:
            mapeo[campo] = indice

    indice_cp = next((i for i, h in enumerate(normalizados) if h in ALIAS_CODIGO_PROVEEDOR), None)
    if indice_cp is not None:
        mapeo["codigo_proveedor"] = indice_cp

    if faltantes:
        raise ColumnasNoDetectadas(faltantes, encabezados)

    return mapeo


def parsear_costo(valor):
    """
    Tolera: números ya parseados por openpyxl (int/float), '1234.56',
    '1234,56' y '1.234,56' (formato argentino con separador de miles).
    Devuelve None si no se puede interpretar como un costo válido.
    """
    if valor is None or valor == "":
        return None

    if isinstance(valor, (int, float)):
        try:
            return Decimal(str(valor)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None

    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if not texto:
        return None

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def leer_excel(archivo):
    """
    Generador que devuelve (numero_de_fila, dict_crudo) por cada fila con
    datos del Excel. numero_de_fila es 1-indexado tal como se ve en Excel
    (la fila 1 es el encabezado), para poder referenciarla si hay dudas.
    """
    libro = openpyxl.load_workbook(archivo, data_only=True, read_only=True)
    try:
        hoja = libro.active
        filas = hoja.iter_rows(values_only=True)
        encabezados = next(filas, [])
        mapeo = detectar_columnas(encabezados)

        for numero, fila in enumerate(filas, start=2):
            if fila is None or all(celda is None for celda in fila):
                continue

            def obtener(campo):
                indice = mapeo.get(campo)
                if indice is None or indice >= len(fila):
                    return None
                return fila[indice]

            yield numero, {
                "marca": str(obtener("marca") or "").strip(),
                "codigo": str(obtener("codigo") or "").strip(),
                "nombre": str(obtener("nombre") or "").strip(),
                "costo_crudo": obtener("costo"),
                "codigo_proveedor": str(obtener("codigo_proveedor") or "").strip(),
            }
    finally:
        libro.close()
