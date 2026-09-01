"""
Llamadas a la API de Claude para apps.imports (Etapa 11).

Es el ÚNICO módulo del app que habla con la red/Anthropic. parsing.py sigue
siendo 100% local (sin IA, sin DB); services.py orquesta cuándo llamar acá
(cache de ProveedorColumnMapping, fallback local→IA) y qué hacer con el
resultado. Toda falla de la API (timeout, rate limit, respuesta inválida)
se colapsa en ExtraccionIAError para que el resto del código tenga un solo
tipo de excepción que atender — el mismo criterio que EnvioOrdenCompraError
en apps.purchasing.mailing.

Nunca se le manda a Claude el archivo completo de Excel/CSV, ni la API key
queda en ningún campo persistido (AuditLog, ImportacionListaPrecios): solo
se guarda la respuesta ya estructurada (ver ImportacionListaPrecios.ia_resultado).
"""

import base64

import anthropic
from django.conf import settings

TIMEOUT_SEGUNDOS = 60.0

MEDIA_TYPE_IMAGEN = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

CAMPOS_CANONICOS = (
    "marca",
    "codigo",
    "nombre",
    "descripcion",
    "costo",
    "codigo_proveedor",
    "unidad",
    "categoria",
)

TOOL_MAPEAR_COLUMNAS = {
    "name": "mapear_columnas",
    "description": (
        "Mapea los encabezados de una planilla de precios de un proveedor a "
        "los campos canónicos del catálogo de ARQCLIMA."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "marca": {"type": ["integer", "null"]},
            "codigo": {"type": ["integer", "null"]},
            "nombre": {"type": ["integer", "null"]},
            "descripcion": {"type": ["integer", "null"]},
            "costo": {"type": ["integer", "null"]},
            "codigo_proveedor": {"type": ["integer", "null"]},
            "unidad": {"type": ["integer", "null"]},
            "categoria": {"type": ["integer", "null"]},
            "advertencias": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "marca",
            "codigo",
            "nombre",
            "descripcion",
            "costo",
            "codigo_proveedor",
            "unidad",
            "categoria",
            "advertencias",
        ],
        "additionalProperties": False,
    },
}

TOOL_EXTRAER_LISTA_PRECIOS = {
    "name": "extraer_lista_precios",
    "description": (
        "Extrae la lista completa de productos de un documento/imagen/texto de "
        "un proveedor de climatización, con sus datos de catálogo y costo."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "productos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "marca": {"type": "string"},
                        "codigo": {"type": "string"},
                        "nombre": {"type": "string"},
                        "descripcion": {"type": "string"},
                        "costo_texto": {"type": "string"},
                        "codigo_proveedor": {"type": "string"},
                        "unidad": {"type": "string"},
                        "categoria": {"type": "string"},
                        "nota": {"type": "string"},
                    },
                    "required": [
                        "marca",
                        "codigo",
                        "nombre",
                        "descripcion",
                        "costo_texto",
                        "codigo_proveedor",
                        "unidad",
                        "categoria",
                        "nota",
                    ],
                    "additionalProperties": False,
                },
            },
            "advertencias": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["productos", "advertencias"],
        "additionalProperties": False,
    },
}

_SISTEMA_MAPEO = (
    "Sos un asistente que identifica qué columna de una planilla de precios de "
    "un proveedor de climatización corresponde a cada campo canónico del "
    "catálogo de ARQCLIMA:\n"
    "- marca: fabricante del producto.\n"
    "- codigo: código oficial del fabricante (nunca un código interno inventado "
    "por el proveedor o por ARQCLIMA salvo que sea lo único disponible).\n"
    "- nombre: nombre o descripción corta del producto.\n"
    "- descripcion: texto descriptivo más largo, si existe una columna aparte.\n"
    "- costo: precio de costo/lista/neto que ARQCLIMA le paga al proveedor. Si "
    "hay precio de lista Y precio bonificado/neto en columnas separadas, "
    "preferí el bonificado/neto como costo.\n"
    "- codigo_proveedor: código interno del proveedor, si es distinto del "
    "código de fábrica.\n"
    "- unidad: unidad de medida (unidad, m, m2, kg, litro, caja, rollo, par, kit).\n"
    "- categoria: familia o rubro del producto.\n\n"
    "Devolvé el índice de columna (0-based) de cada campo que puedas identificar "
    "con confianza, o null si esa columna no existe en la planilla. No inventes "
    "una columna que no está. Si algo es ambiguo, dejalo en null y explicá por "
    "qué en advertencias."
)

_SISTEMA_EXTRACCION = (
    "Sos un asistente que extrae listas de precios de proveedores de "
    "climatización (calefacción, piso radiante, calderas, repuestos) a partir "
    "de un documento, una imagen o un texto estructurado.\n\n"
    "Para cada producto real que encuentres, completá: marca (fabricante), "
    "codigo (código oficial de fábrica, nunca inventado), nombre, descripcion, "
    "costo_texto (el precio tal cual aparece en el original, con separadores y "
    "símbolos incluidos — no hagas la conversión a número vos), codigo_proveedor "
    "(si es distinto del código de fábrica), unidad y categoria. Dejá vacío "
    "('') cualquier campo que no puedas determinar; nunca inventes un valor. Si "
    "hay precio de lista Y precio bonificado/neto, usá el bonificado/neto como "
    "costo_texto. Usá 'nota' para cualquier aclaración puntual sobre esa fila "
    "(ambigüedad, texto poco legible, '*NUEVO*'/'PROXIMAMENTE' u otras marcas "
    "del proveedor que no son parte del nombre del producto). No incluyas como "
    "producto ningún bloque que sea solo ruido (datos del cliente, fecha, "
    "condiciones comerciales, totales). Usá 'advertencias' para cualquier "
    "problema general de lectura (páginas o secciones ilegibles, dudas sobre "
    "la estructura del documento).\n\n"
    "No todos los catálogos son una tabla de filas y columnas: muchos "
    "proveedores arman el documento como una GRILLA DE TARJETAS/RECUADROS, "
    "con un producto por caja (imagen + nombre + código + precio dentro de "
    "un recuadro propio, ej. 'VDR 01 / USD 11.04'), varias cajas por página "
    "en dos o más columnas. Extraé de esa estructura exactamente igual que "
    "de una tabla: cada recuadro con código y precio es una fila de "
    "producto. Es común que el margen tenga columnas decorativas de texto "
    "vertical, una letra por línea (ej. 'L / L / A / V / E / Y / ...') — "
    "son títulos de sección deformados por el diseño, no son productos ni "
    "categorías; ignoralos.\n\n"
    "Si el documento parece ser una lista de precios pero terminás sin "
    "poder extraer ningún producto, NUNCA devuelvas 'productos' y "
    "'advertencias' vacíos a la vez: contá siempre en 'advertencias' por "
    "qué no pudiste (estructura no reconocida, tipo de layout, calidad de "
    "imagen, u otro motivo concreto)."
)


class ExtraccionIAError(RuntimeError):
    """Cualquier falla al llamar a Claude: API key ausente, timeout, rate "
    "limit, error del servidor, o una respuesta que no se pudo interpretar."""


MAX_REINTENTOS = 1


def _cliente():
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ExtraccionIAError(
            "No hay una ANTHROPIC_API_KEY configurada; no se puede usar IA para esta importación."
        )
    # max_retries explícito (no el default implícito del SDK, que es 2):
    # mismo criterio de "nada implícito" del resto del proyecto. Con el
    # default, un timeout podía generar hasta 3 requests HTTP reales sin
    # que quedara ningún rastro de cuántas veces pegó realmente a la API
    # (encontrado al intentar contar llamadas reales durante el diagnóstico
    # de Uriarte Taldea). Con 1, como máximo son 2: el intento inicial más
    # un reintento — alcanza para no perder la corrida por un error
    # transitorio de red sin volver la cuenta de llamadas reales opaca.
    return anthropic.Anthropic(api_key=api_key).with_options(
        timeout=TIMEOUT_SEGUNDOS, max_retries=MAX_REINTENTOS
    )


def _llamar_tool_forzado(*, model, system, content, tool, max_tokens, thinking=None):
    cliente = _cliente()
    kwargs = {}
    if thinking is not None:
        kwargs["thinking"] = thinking
    try:
        respuesta = cliente.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}],
            **kwargs,
        )
    except anthropic.RateLimitError as exc:
        raise ExtraccionIAError(
            "Se alcanzó el límite de uso de la API de Claude. Probá de nuevo en unos minutos."
        ) from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ExtraccionIAError(
                "El servicio de Claude tuvo un error. Probá de nuevo en unos minutos."
            ) from exc
        raise ExtraccionIAError(f"La API de Claude rechazó la solicitud: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        # APITimeoutError hereda de APIConnectionError (verificado contra
        # el anthropic==1.2.0 instalado, no de memoria): no hace falta un
        # except propio, alcanza con distinguir el mensaje acá adentro.
        if isinstance(exc, anthropic.APITimeoutError):
            raise ExtraccionIAError(
                f"La API de Claude tardó demasiado en responder (más de "
                f"{TIMEOUT_SEGUNDOS:.0f}s). Probá de nuevo."
            ) from exc
        raise ExtraccionIAError(
            "No se pudo conectar con la API de Claude. Verificá la conexión de red."
        ) from exc

    if respuesta.stop_reason == "max_tokens":
        raise ExtraccionIAError(
            "La respuesta de Claude se cortó por límite de tokens; la "
            "extracción puede estar incompleta."
        )

    bloque = next(
        (b for b in respuesta.content if b.type == "tool_use" and b.name == tool["name"]),
        None,
    )
    if bloque is None:
        raise ExtraccionIAError(
            "Claude no devolvió el resultado estructurado esperado para esta importación."
        )

    uso = respuesta.usage
    meta = {
        "modelo": respuesta.model,
        "input_tokens": uso.input_tokens,
        "output_tokens": uso.output_tokens,
    }
    return bloque.input, meta


def _texto_mapeo_columnas(encabezados, muestra_filas):
    lineas = ["Encabezados de la planilla (índice: valor):"]
    for indice, valor in enumerate(encabezados):
        lineas.append(f"{indice}: {valor}")
    lineas.append("")
    lineas.append("Filas de muestra (índice de columna: valor):")
    for numero, fila in enumerate(muestra_filas, start=1):
        celdas = " | ".join(f"{indice}: {valor}" for indice, valor in enumerate(fila))
        lineas.append(f"Fila {numero}: {celdas}")
    return "\n".join(lineas)


def mapear_columnas(encabezados, muestra_filas):
    """
    Devuelve (mapeo, advertencias, meta). `mapeo` tiene la misma forma que
    parsing.detectar_columnas(): campo canónico → índice de columna (0-based)
    o ausente si Claude no lo pudo determinar.
    """
    texto = _texto_mapeo_columnas(encabezados, muestra_filas)
    entrada, meta = _llamar_tool_forzado(
        model=settings.ANTHROPIC_MODEL_HAIKU,
        system=_SISTEMA_MAPEO,
        content=[{"type": "text", "text": texto}],
        tool=TOOL_MAPEAR_COLUMNAS,
        max_tokens=1024,
    )
    advertencias = list(entrada.get("advertencias") or [])
    mapeo = {
        campo: entrada[campo]
        for campo in CAMPOS_CANONICOS
        if entrada.get(campo) is not None
    }
    return mapeo, advertencias, meta


def _bloque_contenido(tipo_contenido, contenido, extension_imagen):
    if tipo_contenido == "pdf":
        datos_b64 = base64.standard_b64encode(contenido).decode("ascii")
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": datos_b64,
            },
        }
    if tipo_contenido == "imagen":
        media_type = MEDIA_TYPE_IMAGEN.get((extension_imagen or "").lower())
        if media_type is None:
            raise ExtraccionIAError(
                f"Formato de imagen no soportado para extracción con IA: '{extension_imagen}'."
            )
        datos_b64 = base64.standard_b64encode(contenido).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": datos_b64},
        }
    raise ValueError(f"tipo_contenido inválido: {tipo_contenido!r}")


def extraer_lista_precios(contenido, tipo_contenido, *, extension_imagen=None):
    """
    tipo_contenido: "pdf" | "imagen" | "docx_texto" | "excel_celdas".
    Para "pdf"/"imagen", `contenido` son bytes. Para "docx_texto"/"excel_celdas",
    `contenido` es texto ya extraído localmente (nunca el binario original).

    Devuelve (productos, advertencias, meta). Cada producto es un dict con las
    claves del schema (marca/codigo/nombre/descripcion/costo_texto/...).
    """
    if tipo_contenido in ("pdf", "imagen"):
        bloque = _bloque_contenido(tipo_contenido, contenido, extension_imagen)
        content = [bloque, {"type": "text", "text": "Extraé la lista de productos de este archivo."}]
    elif tipo_contenido in ("docx_texto", "excel_celdas"):
        content = [{"type": "text", "text": contenido}]
    else:
        raise ValueError(f"tipo_contenido inválido: {tipo_contenido!r}")

    entrada, meta = _llamar_tool_forzado(
        model=settings.ANTHROPIC_MODEL_SONNET,
        system=_SISTEMA_EXTRACCION,
        content=content,
        tool=TOOL_EXTRAER_LISTA_PRECIOS,
        # 64000 (no 16000): el límite real del modelo es 128k y el costo es
        # por tokens generados, no por el techo configurado — no cuesta de
        # más a menos que haga falta ese volumen. Encontrado con un caso
        # real (Uriarte Taldea, catálogo en grilla de ~150 productos) que
        # cortaba por stop_reason="max_tokens" con el límite viejo.
        max_tokens=64000,
        # thinking: se probó deshabilitado acá (la hipótesis era que en
        # Sonnet 5 el razonamiento extendido comparte presupuesto con
        # max_tokens y compite con el output de una extracción estructurada
        # con tool_choice forzado) y salió MAL en la práctica, no en teoría:
        # contra Uriarte Taldea (catálogo denso de ~150 ítems en tarjetas
        # repartidas en 10 páginas) devolvió solo 2 productos, uno de ellos
        # un objeto vacío, y una advertencia sin sentido ("placeholder") —
        # confirmado con la API real, no una suposición. Para un documento
        # visualmente simple el thinking no aporta nada, pero para uno denso
        # y disperso como este parece ser lo que evita que el modelo se
        # conforme con una extracción parcial. Se deja sin pasar el
        # parámetro (comportamiento adaptativo por default de Sonnet 5) — no
        # volver a deshabilitarlo acá sin repetir esta medición.
    )
    productos = entrada.get("productos") or []
    advertencias = list(entrada.get("advertencias") or [])
    if not productos and not advertencias:
        # Red de contención: el prompt le pide a Claude que siempre explique
        # en 'advertencias' por qué no extrajo productos, pero no depende de
        # que esa instrucción funcione — un modelo puede devolver los dos
        # arrays vacíos igual (caso real: Importación #10, Uriarte Taldea,
        # catálogo en grilla de tarjetas). Sin esto, el preview queda
        # silenciosamente vacío de contexto y el usuario ve 0 filas sin
        # ninguna pista de qué pasó.
        advertencias = [
            "Claude no extrajo productos de este documento y no indicó el "
            "motivo; revisar manualmente el archivo original."
        ]
    return productos, advertencias, meta
