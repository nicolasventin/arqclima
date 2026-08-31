import hashlib
import unicodedata
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Categoria, Marca, Producto, ProductoProveedor, UnidadMedida
from apps.catalog.permissions import puede_crear_producto
from apps.pricing.models import Moneda
from apps.pricing.permissions import puede_registrar_costo
from apps.pricing.services import costo_actual, registrar_costo

from .ai import ExtraccionIAError, extraer_lista_precios, mapear_columnas
from .models import (
    ImportacionFila,
    ImportacionImagen,
    ImportacionListaPrecios,
    ProveedorColumnMapping,
)
from .parsing import (
    CAMPOS_REQUERIDOS,
    ColumnasNoDetectadas,
    FilaAnalizada,
    analizar_archivo,
    detectar_moneda_texto,
    extraer_filas_con_mapeo,
    parsear_costo,
)


UNIDAD_ALIASES = {
    UnidadMedida.UNIDAD: {"unidad", "un", "u", "unit", "pieza", "pza"},
    UnidadMedida.METRO: {"metro", "metros", "m", "mt"},
    UnidadMedida.METRO_CUADRADO: {"m2", "m²", "metro cuadrado", "metros cuadrados"},
    UnidadMedida.KILOGRAMO: {"kg", "kilogramo", "kilogramos"},
    UnidadMedida.LITRO: {"l", "lt", "litro", "litros"},
    UnidadMedida.CAJA: {"caja", "cajas", "cj"},
    UnidadMedida.ROLLO: {"rollo", "rollos"},
    UnidadMedida.PAR: {"par", "pares"},
    UnidadMedida.KIT: {"kit", "juego", "set"},
}


def _normalizar(texto):
    texto = str(texto or "").strip().lower()
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _buscar_marca(nombre):
    if not nombre:
        return None
    return Marca.objects.filter(nombre__iexact=nombre.strip()).first()


def _buscar_categoria(nombre):
    if not nombre:
        return None
    return Categoria.objects.filter(nombre__iexact=nombre.strip()).first()


def _unidad_desde_texto(texto):
    if not texto:
        return UnidadMedida.UNIDAD
    normalizado = _normalizar(texto)
    for valor, aliases in UNIDAD_ALIASES.items():
        if normalizado in {_normalizar(alias) for alias in aliases}:
            return valor
    return None


def _buscar_producto(marca, codigo):
    if marca is None or not codigo:
        return None
    return Producto.objects.filter(marca=marca, codigo__iexact=codigo.strip()).first()


def _buscar_vinculo_por_codigo_proveedor(proveedor, codigo_proveedor):
    if not codigo_proveedor:
        return None
    return (
        ProductoProveedor.objects.filter(
            proveedor=proveedor,
            codigo_proveedor__iexact=codigo_proveedor.strip(),
        )
        .select_related("producto", "producto__marca")
        .first()
    )


def _etiqueta_moneda(moneda):
    return "U$S" if moneda == Moneda.USD else "$"


def _datos_normalizados(cruda):
    costo_crudo = cruda.get("costo_crudo", cruda.get("costo", ""))
    # cruda.get("moneda"): señal por number_format de Excel (confiable,
    # armada en parsing.py). Si no vino ninguna (CSV, IA, o Excel sin
    # number_format de moneda), se cae a texto (costo_texto de Sonnet, o
    # el símbolo escrito a mano en la celda) y por último a ARS.
    moneda = cruda.get("moneda") or detectar_moneda_texto(costo_crudo) or Moneda.ARS
    return {
        "marca": str(cruda.get("marca") or "").strip(),
        "codigo": str(cruda.get("codigo") or "").strip(),
        "nombre": str(cruda.get("nombre") or "").strip(),
        "descripcion": str(cruda.get("descripcion") or "").strip(),
        "costo_crudo": costo_crudo,
        "costo": parsear_costo(costo_crudo),
        "moneda": moneda,
        "codigo_proveedor": str(cruda.get("codigo_proveedor") or "").strip(),
        "unidad": str(cruda.get("unidad") or "").strip(),
        "categoria": str(cruda.get("categoria") or "").strip(),
    }


def _clasificar(importacion, cruda, usuario):
    datos = _datos_normalizados(cruda)
    marca_texto = datos["marca"]
    codigo = datos["codigo"]
    nombre = datos["nombre"]
    costo = datos["costo"]
    moneda = datos["moneda"]
    codigo_proveedor = datos["codigo_proveedor"]
    unidad_texto = datos["unidad"]

    if not codigo or not nombre or costo is None or costo <= 0:
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.ERROR,
            "detalle": "Faltan Código/Nombre o el costo no es válido y mayor a cero.",
            "producto": None,
        }

    unidad = _unidad_desde_texto(unidad_texto)
    if unidad_texto and unidad is None:
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.PARA_REVISAR,
            "detalle": f"No se reconoce la unidad de medida '{unidad_texto}'.",
            "producto": None,
        }

    marca = _buscar_marca(marca_texto)
    producto_por_texto = _buscar_producto(marca, codigo)
    vinculo_por_codigo = _buscar_vinculo_por_codigo_proveedor(
        importacion.proveedor,
        codigo_proveedor,
    )

    if vinculo_por_codigo is not None:
        producto = vinculo_por_codigo.producto
        if producto_por_texto is not None and producto_por_texto.pk != producto.pk:
            return {
                "datos": datos,
                "categoria": ImportacionFila.Categoria.PARA_REVISAR,
                "detalle": (
                    f"El código de proveedor '{codigo_proveedor}' ya está vinculado a "
                    f"'{producto}', pero Marca+Código sugieren '{producto_por_texto}'."
                ),
                "producto": None,
            }
    else:
        producto = producto_por_texto

    if producto is None:
        if not marca_texto:
            return {
                "datos": datos,
                "categoria": ImportacionFila.Categoria.PARA_REVISAR,
                "detalle": (
                    "No se pudo resolver la marca. Completala antes de crear un producto nuevo "
                    "o asegurate de que el código de proveedor ya esté vinculado."
                ),
                "producto": None,
            }
        if not puede_crear_producto(usuario):
            return {
                "datos": datos,
                "categoria": ImportacionFila.Categoria.PARA_REVISAR,
                "detalle": "No tenés permiso para dar de alta productos nuevos.",
                "producto": None,
            }
        if marca is None and not usuario.has_perm("catalog.add_marca"):
            return {
                "datos": datos,
                "categoria": ImportacionFila.Categoria.PARA_REVISAR,
                "detalle": f"Requiere crear la marca '{marca_texto}' y no tenés permiso.",
                "producto": None,
            }

        detalle = ""
        if marca is None:
            detalle = f"También va a crear la marca '{marca_texto}'."
        if datos["categoria"] and _buscar_categoria(datos["categoria"]) is None:
            extra = (
                f"La categoría del proveedor '{datos['categoria']}' no existe en ARQCLIMA; "
                "el producto se creará sin categoría."
            )
            detalle = f"{detalle} {extra}".strip()

        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.NUEVO_PRODUCTO,
            "detalle": detalle,
            "producto": None,
        }

    if not puede_registrar_costo(usuario, producto):
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.PARA_REVISAR,
            "detalle": "No tenés permiso para modificar costos de este producto.",
            "producto": producto,
        }

    if nombre.strip().casefold() != producto.nombre.strip().casefold():
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.PARA_REVISAR,
            "detalle": f"Nombre distinto al registrado: '{nombre}' vs '{producto.nombre}'.",
            "producto": producto,
        }

    if unidad_texto and unidad and producto.unidad_medida != unidad:
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.PARA_REVISAR,
            "detalle": (
                f"Unidad distinta: el archivo dice '{unidad_texto}' y el producto está "
                f"registrado como '{producto.get_unidad_medida_display()}'."
            ),
            "producto": producto,
        }

    vinculo = vinculo_por_codigo or ProductoProveedor.objects.filter(
        producto=producto,
        proveedor=importacion.proveedor,
    ).first()

    if vinculo is None:
        return {
            "datos": datos,
            "categoria": ImportacionFila.Categoria.NUEVO_VINCULO,
            "detalle": "",
            "producto": producto,
        }

    historial = costo_actual(vinculo)
    if historial is not None and historial.costo == costo and historial.moneda == moneda:
        categoria = ImportacionFila.Categoria.SIN_CAMBIOS
        detalle = ""
    else:
        categoria = ImportacionFila.Categoria.ACTUALIZA_COSTO
        etiqueta_nueva = _etiqueta_moneda(moneda)
        detalle = (
            f"{_etiqueta_moneda(historial.moneda)} {historial.costo} → {etiqueta_nueva} {costo}"
            if historial is not None
            else f"Primer costo: {etiqueta_nueva} {costo}"
        )
    return {
        "datos": datos,
        "categoria": categoria,
        "detalle": detalle,
        "producto": producto,
    }


def _crear_fila_desde_analisis(importacion, fila_analizada, usuario):
    clasificacion = _clasificar(importacion, fila_analizada.datos, usuario)
    datos = clasificacion["datos"]
    categoria = clasificacion["categoria"]

    incluir = categoria not in (
        ImportacionFila.Categoria.ERROR,
        ImportacionFila.Categoria.PARA_REVISAR,
        ImportacionFila.Categoria.SIN_CAMBIOS,
    )
    if fila_analizada.confianza in ("media", "baja"):
        incluir = False

    detalle = clasificacion["detalle"]
    if fila_analizada.confianza in ("media", "baja") and categoria not in (
        ImportacionFila.Categoria.ERROR,
        ImportacionFila.Categoria.PARA_REVISAR,
    ):
        nota = (
            "Extracción automática de confianza "
            f"{fila_analizada.confianza}; verificar visualmente antes de incluir."
        )
        detalle = f"{detalle} {nota}".strip()
    if fila_analizada.nota_ia:
        detalle = f"{detalle} IA: {fila_analizada.nota_ia}".strip()

    return ImportacionFila.objects.create(
        importacion=importacion,
        numero_fila=fila_analizada.numero,
        origen=fila_analizada.origen,
        confianza=fila_analizada.confianza,
        marca_texto=datos["marca"],
        codigo=datos["codigo"],
        nombre_texto=datos["nombre"],
        descripcion_texto=datos["descripcion"],
        costo_texto=(
            str(datos["costo_crudo"]) if datos["costo_crudo"] is not None else ""
        ),
        costo=datos["costo"],
        moneda=datos["moneda"],
        codigo_proveedor_texto=datos["codigo_proveedor"],
        unidad_texto=datos["unidad"],
        categoria_texto=datos["categoria"],
        categoria=categoria,
        detalle=detalle,
        producto=clasificacion["producto"],
        incluir=incluir,
    )


def _clave_duplicado(fila):
    if fila.codigo_proveedor_texto:
        return ("proveedor", _normalizar(fila.codigo_proveedor_texto))
    if fila.codigo:
        return (
            "producto",
            _normalizar(fila.marca_texto),
            _normalizar(fila.codigo),
        )
    return None


def _restaurar_filas_marcadas_por_duplicado(importacion, usuario):
    """
    Si una corrección manual rompe un conflicto de duplicados, restaura la
    clasificación base de las filas que habían quedado bloqueadas solo por
    ese conflicto antes de volver a detectar duplicados.
    """
    prefijos = (
        "El mismo producto/código de proveedor aparece repetido",
        "Duplicado idéntico de ",
    )
    for fila in importacion.filas.all():
        if not any((fila.detalle or "").startswith(prefijo) for prefijo in prefijos):
            continue
        clasificacion = _clasificar(
            importacion,
            {
                "marca": fila.marca_texto,
                "codigo": fila.codigo,
                "nombre": fila.nombre_texto,
                "descripcion": fila.descripcion_texto,
                "costo_crudo": fila.costo,
                "codigo_proveedor": fila.codigo_proveedor_texto,
                "unidad": fila.unidad_texto,
                "categoria": fila.categoria_texto,
            },
            usuario,
        )
        fila.categoria = clasificacion["categoria"]
        fila.detalle = clasificacion["detalle"]
        fila.producto = clasificacion["producto"]
        fila.incluir = (
            fila.categoria not in (
                ImportacionFila.Categoria.ERROR,
                ImportacionFila.Categoria.PARA_REVISAR,
                ImportacionFila.Categoria.SIN_CAMBIOS,
            )
            and fila.confianza not in (
                ImportacionFila.Confianza.MEDIA,
                ImportacionFila.Confianza.BAJA,
            )
        )
        fila.save(
            update_fields=["categoria", "detalle", "producto", "incluir"]
        )


def _marcar_duplicados(importacion):
    grupos = {}
    for fila in importacion.filas.all():
        clave = _clave_duplicado(fila)
        if clave is not None:
            grupos.setdefault(clave, []).append(fila)

    for filas in grupos.values():
        if len(filas) < 2:
            continue
        firmas = {
            (
                _normalizar(fila.marca_texto),
                _normalizar(fila.codigo),
                _normalizar(fila.nombre_texto),
                fila.costo,
                _normalizar(fila.codigo_proveedor_texto),
            )
            for fila in filas
        }
        if len(firmas) == 1:
            primera = filas[0]
            for duplicada in filas[1:]:
                duplicada.categoria = ImportacionFila.Categoria.SIN_CAMBIOS
                duplicada.incluir = False
                duplicada.detalle = (
                    f"Duplicado idéntico de {primera.origen or 'archivo'} "
                    f"fila {primera.numero_fila}; se importa una sola vez."
                )
                duplicada.save(update_fields=["categoria", "incluir", "detalle"])
        else:
            origenes = ", ".join(
                f"{fila.origen or 'archivo'} fila {fila.numero_fila}"
                for fila in filas[:5]
            )
            for fila in filas:
                fila.categoria = ImportacionFila.Categoria.PARA_REVISAR
                fila.incluir = False
                fila.detalle = (
                    "El mismo producto/código de proveedor aparece repetido con valores distintos "
                    f"({origenes}). Corregí o excluí las filas antes de confirmar."
                )
                fila.save(update_fields=["categoria", "incluir", "detalle"])


def _guardar_imagenes(importacion, imagenes):
    guardadas = 0
    for indice, imagen in enumerate(imagenes, start=1):
        nombre = f"importacion-{importacion.pk}-{indice}.{imagen.extension}"
        try:
            objeto = ImportacionImagen(
                importacion=importacion,
                origen=imagen.origen,
                numero_fila_origen=imagen.numero_fila_origen,
                nombre_original=imagen.nombre_original,
                ancho=imagen.ancho,
                alto=imagen.alto,
                huella_sha256=imagen.huella_sha256,
            )
            objeto.archivo.save(nombre, ContentFile(imagen.contenido), save=False)
            objeto.save()
            guardadas += 1
        except IntegrityError:
            continue
    return guardadas


def _hash_encabezados(encabezados):
    normalizado = "|".join(_normalizar(str(e)) for e in encabezados)
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def _obtener_mapeo_columnas(proveedor, encabezados, muestra_filas):
    """
    Cache-first: si este proveedor ya resolvió este mismo header alguna vez
    (ProveedorColumnMapping), no se vuelve a llamar a Haiku. `meta["cache"]`
    distingue en ia_resultado si hubo o no una llamada real a la API.
    """
    hash_actual = _hash_encabezados(encabezados)
    cacheado = ProveedorColumnMapping.objects.filter(
        proveedor=proveedor, encabezados_hash=hash_actual
    ).first()
    if cacheado is not None:
        return cacheado.mapeo, [], {"cache": True}

    mapeo, advertencias, meta = mapear_columnas(encabezados, muestra_filas)
    faltantes = [campo for campo in CAMPOS_REQUERIDOS if campo not in mapeo]
    if faltantes:
        raise ExtraccionIAError(
            f"Claude no pudo identificar las columnas obligatorias ({', '.join(faltantes)})."
        )

    try:
        ProveedorColumnMapping.objects.create(
            proveedor=proveedor,
            encabezados=list(encabezados),
            encabezados_hash=hash_actual,
            mapeo=mapeo,
        )
    except IntegrityError:
        # Otra importación concurrente ya cacheó el mismo header primero;
        # el mapeo que ya tenemos en memoria sigue siendo válido igual.
        pass

    meta["cache"] = False
    return mapeo, advertencias, meta


def _filas_desde_extraccion_ia(productos, origen):
    filas = []
    for numero, producto in enumerate(productos, start=1):
        filas.append(
            FilaAnalizada(
                numero=numero,
                origen=f"{origen} (IA)",
                confianza=ImportacionFila.Confianza.MEDIA,
                nota_ia=(producto.get("nota") or "").strip(),
                datos={
                    "marca": (producto.get("marca") or "").strip(),
                    "codigo": (producto.get("codigo") or "").strip(),
                    "nombre": (producto.get("nombre") or "").strip(),
                    "descripcion": (producto.get("descripcion") or "").strip(),
                    "costo_crudo": producto.get("costo_texto") or "",
                    "codigo_proveedor": (producto.get("codigo_proveedor") or "").strip(),
                    "unidad": (producto.get("unidad") or "").strip(),
                    "categoria": (producto.get("categoria") or "").strip(),
                },
            )
        )
    return filas


def resolver_pendientes_ia(resultado, importacion):
    """
    Resuelve, uno por uno, los PendienteIA que parsing.py no pudo cerrar
    localmente. Es la única función de services.py que llama a apps.imports.ai
    (red) combinado con ProveedorColumnMapping (DB) — parsing.py no toca
    ninguna de las dos cosas.

    Un pendiente que falla (timeout, rate limit, respuesta inválida) no
    interrumpe el resto de la importación: se agrega una advertencia y esa
    fuente en particular queda sin filas, igual que un archivo que no se
    pudo analizar antes de que existiera este camino.

    El except de acá abajo solo atrapa ExtraccionIAError (fallas externas:
    red, rate limit, respuesta de Claude inválida) a propósito, aunque
    extraer_filas_con_mapeo() —función 100% local, sin red ni DB— quede
    sintácticamente dentro del mismo try. Mismo criterio que el resto del
    proyecto (regla general de la sesión: no envolver en manejo de errores
    algo que "no debería pasar"): si esa función local lanza algo que no sea
    ExtraccionIAError, es un bug real de nuestro código operando sobre un
    mapeo ya resuelto/cacheado, no una falla esperable del archivo o de la
    API — debe cortar el resto del loop y hacer ruido en vez de disfrazarse
    de "no se pudo procesar automáticamente con IA", que sería engañoso.
    """
    meta_llamadas = []
    for pendiente in resultado.pendientes_ia:
        try:
            if pendiente.tipo == "mapeo_columnas":
                mapeo, advertencias, meta = _obtener_mapeo_columnas(
                    importacion.proveedor,
                    pendiente.encabezados,
                    pendiente.muestra_filas,
                )
                # Fuera del alcance real del except: ver nota arriba.
                filas, adv_filas = extraer_filas_con_mapeo(
                    pendiente.matriz_restante,
                    pendiente.origen,
                    mapeo,
                    confianza="alta",
                )
                resultado.filas.extend(filas)
                resultado.advertencias.extend(advertencias)
                resultado.advertencias.extend(adv_filas)
            elif pendiente.tipo == "extraccion":
                productos, advertencias, meta = extraer_lista_precios(
                    pendiente.contenido,
                    pendiente.tipo_contenido,
                    extension_imagen=pendiente.extension_imagen,
                )
                resultado.filas.extend(_filas_desde_extraccion_ia(productos, pendiente.origen))
                resultado.advertencias.extend(advertencias)
            else:  # pragma: no cover - defensivo, no debería pasar
                continue
        except ExtraccionIAError as exc:
            resultado.advertencias.append(
                f"{pendiente.origen}: no se pudo procesar automáticamente con IA ({exc})."
            )
            continue

        importacion.usa_ia = True
        meta_llamadas.append({"origen": pendiente.origen, "tipo": pendiente.tipo, **meta})

    return meta_llamadas


def procesar_importacion(importacion):
    """
    Analiza el archivo, genera preview de filas e imágenes y clasifica.

    Sigue siendo una operación de PREVIEW: no escribe Producto,
    ProductoProveedor ni HistorialCosto. Lo que parsing.py no pudo resolver
    de forma local (PendienteIA) se resuelve acá con resolver_pendientes_ia
    antes de decidir si la importación tiene algo para mostrar.
    """
    importacion.filas.all().delete()
    importacion.imagenes.all().delete()

    resultado = analizar_archivo(importacion.archivo, importacion.tipo_archivo)
    meta_llamadas = resolver_pendientes_ia(resultado, importacion)

    if not resultado.filas and not resultado.imagenes:
        raise ColumnasNoDetectadas(
            ["codigo", "nombre", "costo"],
            [],
            origen=importacion.get_tipo_archivo_display(),
        )

    usuario = importacion.cargado_por
    for fila_analizada in resultado.filas:
        _crear_fila_desde_analisis(importacion, fila_analizada, usuario)

    _marcar_duplicados(importacion)
    _guardar_imagenes(importacion, resultado.imagenes)

    requiere_revision = (
        bool(resultado.advertencias)
        or not importacion.filas.exists()
        or importacion.filas.filter(
            categoria__in=[
                ImportacionFila.Categoria.ERROR,
                ImportacionFila.Categoria.PARA_REVISAR,
            ]
        ).exists()
        or importacion.filas.filter(
            confianza__in=[
                ImportacionFila.Confianza.MEDIA,
                ImportacionFila.Confianza.BAJA,
            ]
        ).exists()
    )
    importacion.advertencias_analisis = resultado.advertencias
    importacion.estado_analisis = (
        ImportacionListaPrecios.EstadoAnalisis.REQUIERE_REVISION
        if requiere_revision
        else ImportacionListaPrecios.EstadoAnalisis.COMPLETO
    )
    importacion.analizado_en = timezone.now()
    if meta_llamadas:
        importacion.ia_resultado = meta_llamadas
    importacion.save(
        update_fields=[
            "advertencias_analisis",
            "estado_analisis",
            "analizado_en",
            "usa_ia",
            "ia_resultado",
        ]
    )
    return resultado


def reclasificar_fila(fila, usuario, datos_editados):
    """
    Reanaliza una fila corregida manualmente sin tocar el catálogo.
    """
    clasificacion = _clasificar(fila.importacion, datos_editados, usuario)
    datos = clasificacion["datos"]
    fila.marca_texto = datos["marca"]
    fila.codigo = datos["codigo"]
    fila.nombre_texto = datos["nombre"]
    fila.descripcion_texto = datos["descripcion"]
    fila.costo_texto = str(datos["costo_crudo"] or "")
    fila.costo = datos["costo"]
    fila.moneda = datos["moneda"]
    fila.codigo_proveedor_texto = datos["codigo_proveedor"]
    fila.unidad_texto = datos["unidad"]
    fila.categoria_texto = datos["categoria"]
    fila.categoria = clasificacion["categoria"]
    fila.detalle = clasificacion["detalle"]
    fila.producto = clasificacion["producto"]
    fila.confianza = ImportacionFila.Confianza.REVISADA
    fila.incluir = fila.categoria not in (
        ImportacionFila.Categoria.ERROR,
        ImportacionFila.Categoria.PARA_REVISAR,
        ImportacionFila.Categoria.SIN_CAMBIOS,
    )
    fila.save(
        update_fields=[
            "marca_texto",
            "codigo",
            "nombre_texto",
            "descripcion_texto",
            "costo_texto",
            "costo",
            "moneda",
            "codigo_proveedor_texto",
            "unidad_texto",
            "categoria_texto",
            "categoria",
            "detalle",
            "producto",
            "confianza",
            "incluir",
        ]
    )
    _restaurar_filas_marcadas_por_duplicado(fila.importacion, usuario)
    _marcar_duplicados(fila.importacion)
    return fila




def asignar_marca_filas_sin_marca(importacion, usuario, marca):
    """
    Completa de forma masiva una marca elegida explícitamente por el usuario.

    Está pensado para listas monomarca que no incluyen columna Marca. No
    cambia confianza de extracción ni toca catálogo/costos: solo reclasifica
    el preview con una decisión humana explícita.
    """
    filas = list(
        importacion.filas.filter(marca_texto="").order_by("pk")
    )
    actualizadas = 0

    for fila in filas:
        clasificacion = _clasificar(
            importacion,
            {
                "marca": marca.nombre,
                "codigo": fila.codigo,
                "nombre": fila.nombre_texto,
                "descripcion": fila.descripcion_texto,
                "costo_crudo": fila.costo,
                "codigo_proveedor": fila.codigo_proveedor_texto,
                "unidad": fila.unidad_texto,
                "categoria": fila.categoria_texto,
            },
            usuario,
        )
        datos = clasificacion["datos"]
        fila.marca_texto = datos["marca"]
        fila.categoria = clasificacion["categoria"]
        fila.detalle = clasificacion["detalle"]
        fila.producto = clasificacion["producto"]
        fila.incluir = (
            fila.categoria not in (
                ImportacionFila.Categoria.ERROR,
                ImportacionFila.Categoria.PARA_REVISAR,
                ImportacionFila.Categoria.SIN_CAMBIOS,
            )
            and fila.confianza not in (
                ImportacionFila.Confianza.MEDIA,
                ImportacionFila.Confianza.BAJA,
            )
        )
        fila.save(
            update_fields=[
                "marca_texto",
                "categoria",
                "detalle",
                "producto",
                "incluir",
            ]
        )
        actualizadas += 1

    _restaurar_filas_marcadas_por_duplicado(importacion, usuario)
    _marcar_duplicados(importacion)
    return actualizadas

def _crear_o_resolver_producto(fila, usuario):
    marca = _buscar_marca(fila.marca_texto)
    if marca is None:
        if not usuario.has_perm("catalog.add_marca"):
            return None
        marca = Marca.objects.create(nombre=fila.marca_texto)

    producto = _buscar_producto(marca, fila.codigo)
    if producto is not None:
        return producto

    if not puede_crear_producto(usuario):
        return None

    unidad = _unidad_desde_texto(fila.unidad_texto)
    if fila.unidad_texto and unidad is None:
        return None

    return Producto.objects.create(
        marca=marca,
        codigo=fila.codigo,
        nombre=fila.nombre_texto,
        descripcion=fila.descripcion_texto,
        categoria=_buscar_categoria(fila.categoria_texto),
        unidad_medida=unidad or UnidadMedida.UNIDAD,
        es_repuesto=not usuario.has_perm("catalog.add_producto"),
    )


@transaction.atomic
def confirmar_importacion(importacion, usuario):
    """
    Aplica únicamente filas seguras e incluidas.

    Antes de escribir vuelve a clasificar contra el estado ACTUAL de la
    base. ERROR y PARA_REVISAR jamás se aplican por manipulación del POST.
    """
    importacion = (
        ImportacionListaPrecios.objects.select_for_update()
        .select_related("proveedor")
        .get(pk=importacion.pk)
    )
    if importacion.estado != ImportacionListaPrecios.Estado.PENDIENTE:
        raise ValueError("La importación ya no está pendiente.")

    contadores = {"creados": 0, "actualizados": 0, "omitidos": 0}

    filas = list(
        importacion.filas.filter(incluir=True)
        .select_related("producto")
        .order_by("pk")
    )

    for fila in filas:
        if fila.categoria in (
            ImportacionFila.Categoria.ERROR,
            ImportacionFila.Categoria.PARA_REVISAR,
        ):
            contadores["omitidos"] += 1
            continue

        fresca = _clasificar(
            importacion,
            {
                "marca": fila.marca_texto,
                "codigo": fila.codigo,
                "nombre": fila.nombre_texto,
                "descripcion": fila.descripcion_texto,
                "costo_crudo": fila.costo,
                "moneda": fila.moneda,
                "codigo_proveedor": fila.codigo_proveedor_texto,
                "unidad": fila.unidad_texto,
                "categoria": fila.categoria_texto,
            },
            usuario,
        )
        if fresca["categoria"] in (
            ImportacionFila.Categoria.ERROR,
            ImportacionFila.Categoria.PARA_REVISAR,
        ):
            fila.categoria = fresca["categoria"]
            fila.detalle = fresca["detalle"]
            fila.incluir = False
            fila.producto = fresca["producto"]
            fila.save(update_fields=["categoria", "detalle", "incluir", "producto"])
            contadores["omitidos"] += 1
            continue

        producto = fresca["producto"]
        creado = False
        if producto is None:
            producto = _crear_o_resolver_producto(fila, usuario)
            if producto is None:
                contadores["omitidos"] += 1
                continue
            creado = True

        if not puede_registrar_costo(usuario, producto):
            contadores["omitidos"] += 1
            continue

        vinculo, _ = ProductoProveedor.objects.get_or_create(
            producto=producto,
            proveedor=importacion.proveedor,
            defaults={"codigo_proveedor": fila.codigo_proveedor_texto},
        )
        if fila.codigo_proveedor_texto and not vinculo.codigo_proveedor:
            vinculo.codigo_proveedor = fila.codigo_proveedor_texto
            vinculo.save(update_fields=["codigo_proveedor"])

        historial = costo_actual(vinculo)
        if (
            historial is None
            or historial.costo != fila.costo
            or historial.moneda != fila.moneda
        ):
            registrar_costo(
                vinculo,
                fila.costo,
                usuario,
                origen=f"importación #{importacion.pk}",
                moneda=fila.moneda,
            )
            if not creado:
                contadores["actualizados"] += 1

        if creado:
            contadores["creados"] += 1

        if fila.producto_id != producto.pk:
            fila.producto = producto
            fila.save(update_fields=["producto"])

    importacion.estado = ImportacionListaPrecios.Estado.CONFIRMADA
    importacion.confirmada_por = usuario
    importacion.confirmada_en = timezone.now()
    importacion.save(update_fields=["estado", "confirmada_por", "confirmada_en"])
    return contadores
