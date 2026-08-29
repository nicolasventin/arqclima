import unicodedata
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Categoria, Marca, Producto, ProductoProveedor, UnidadMedida
from apps.catalog.permissions import puede_crear_producto
from apps.pricing.permissions import puede_registrar_costo
from apps.pricing.services import costo_actual, registrar_costo

from .models import ImportacionFila, ImportacionImagen, ImportacionListaPrecios
from .parsing import ColumnasNoDetectadas, analizar_archivo, parsear_costo


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


def _datos_normalizados(cruda):
    costo_crudo = cruda.get("costo_crudo", cruda.get("costo", ""))
    return {
        "marca": str(cruda.get("marca") or "").strip(),
        "codigo": str(cruda.get("codigo") or "").strip(),
        "nombre": str(cruda.get("nombre") or "").strip(),
        "descripcion": str(cruda.get("descripcion") or "").strip(),
        "costo_crudo": costo_crudo,
        "costo": parsear_costo(costo_crudo),
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
    if historial is not None and historial.costo == costo:
        categoria = ImportacionFila.Categoria.SIN_CAMBIOS
        detalle = ""
    else:
        categoria = ImportacionFila.Categoria.ACTUALIZA_COSTO
        detalle = (
            f"$ {historial.costo} → $ {costo}"
            if historial is not None
            else f"Primer costo: $ {costo}"
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


def procesar_importacion(importacion):
    """
    Analiza el archivo, genera preview de filas e imágenes y clasifica.

    Sigue siendo una operación de PREVIEW: no escribe Producto,
    ProductoProveedor ni HistorialCosto.
    """
    importacion.filas.all().delete()
    importacion.imagenes.all().delete()

    resultado = analizar_archivo(importacion.archivo, importacion.tipo_archivo)
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
    importacion.save(
        update_fields=[
            "advertencias_analisis",
            "estado_analisis",
            "analizado_en",
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
        if historial is None or historial.costo != fila.costo:
            registrar_costo(
                vinculo,
                fila.costo,
                usuario,
                origen=f"importación #{importacion.pk}",
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
