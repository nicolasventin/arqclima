from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Marca, Producto, ProductoProveedor
from apps.catalog.permissions import puede_crear_producto
from apps.pricing.permissions import puede_registrar_costo
from apps.pricing.services import costo_actual, registrar_costo

from .models import ImportacionFila, ImportacionListaPrecios
from .parsing import leer_excel, parsear_costo


def _buscar_marca(nombre):
    return Marca.objects.filter(nombre__iexact=nombre).first()


def _buscar_producto(marca, codigo):
    if marca is None or not codigo:
        return None
    return Producto.objects.filter(marca=marca, codigo__iexact=codigo).first()


def _buscar_vinculo_por_codigo_proveedor(proveedor, codigo_proveedor):
    """
    Busca un ProductoProveedor ya existente de este proveedor por su propio
    código (el que se guarda en ProductoProveedor.codigo_proveedor desde la
    Etapa 2). Es el camino rápido para la SEGUNDA importación de un mismo
    proveedor en adelante: no depende de que el texto de marca/nombre se
    escriba exactamente igual que la vez anterior.
    """
    if not codigo_proveedor:
        return None
    return ProductoProveedor.objects.filter(
        proveedor=proveedor, codigo_proveedor__iexact=codigo_proveedor
    ).select_related("producto", "producto__marca").first()


def procesar_importacion(importacion):
    """
    Lee el Excel y crea un ImportacionFila por cada fila con datos,
    clasificada según las 4 categorías de la regla de negocio 4 (nuevos,
    existentes -con o sin cambio de costo-, para revisar, errores). No
    escribe nada en Producto/ProductoProveedor/HistorialCosto: eso pasa
    recién en confirmar_importacion().
    """
    usuario = importacion.cargado_por

    for numero, cruda in leer_excel(importacion.archivo):
        marca_texto = cruda["marca"]
        codigo = cruda["codigo"]
        nombre = cruda["nombre"]
        codigo_proveedor = cruda["codigo_proveedor"]
        costo = parsear_costo(cruda["costo_crudo"])

        base = dict(
            importacion=importacion,
            numero_fila=numero,
            marca_texto=marca_texto,
            codigo=codigo,
            nombre_texto=nombre,
            costo_texto=str(cruda["costo_crudo"]) if cruda["costo_crudo"] is not None else "",
            codigo_proveedor_texto=cruda["codigo_proveedor"],
        )

        if not marca_texto or not codigo or not nombre or costo is None or costo <= 0:
            ImportacionFila.objects.create(
                **base,
                categoria=ImportacionFila.Categoria.ERROR,
                detalle="Faltan datos requeridos o el costo no es válido",
                incluir=False,
            )
            continue

        marca = _buscar_marca(marca_texto)
        producto_por_texto = _buscar_producto(marca, codigo)

        vinculo_por_codigo_proveedor = _buscar_vinculo_por_codigo_proveedor(
            importacion.proveedor, codigo_proveedor
        )

        if vinculo_por_codigo_proveedor is not None:
            producto = vinculo_por_codigo_proveedor.producto
            if producto_por_texto is not None and producto_por_texto.pk != producto.pk:
                ImportacionFila.objects.create(
                    **base, costo=costo,
                    categoria=ImportacionFila.Categoria.PARA_REVISAR,
                    detalle=(
                        f"El código de proveedor '{codigo_proveedor}' ya está vinculado a "
                        f"'{producto}', pero la marca y el código de esta fila sugieren "
                        f"'{producto_por_texto}'. Revisar a mano cuál es el correcto."
                    ),
                    incluir=False,
                )
                continue
        else:
            producto = producto_por_texto

        if producto is None:
            if not puede_crear_producto(usuario):
                categoria = ImportacionFila.Categoria.PARA_REVISAR
                detalle = "No tenés permiso para dar de alta productos nuevos"
            elif marca is None and not usuario.has_perm("catalog.add_marca"):
                categoria = ImportacionFila.Categoria.PARA_REVISAR
                detalle = f"Requiere crear la marca '{marca_texto}' primero (permiso insuficiente)"
            else:
                categoria = ImportacionFila.Categoria.NUEVO_PRODUCTO
                detalle = "" if marca else f"También va a crear la marca '{marca_texto}'"
            ImportacionFila.objects.create(
                **base, costo=costo, categoria=categoria, detalle=detalle,
                incluir=(categoria == ImportacionFila.Categoria.NUEVO_PRODUCTO),
            )
            continue

        if not puede_registrar_costo(usuario, producto):
            ImportacionFila.objects.create(
                **base, costo=costo, producto=producto,
                categoria=ImportacionFila.Categoria.PARA_REVISAR,
                detalle="No tenés permiso para modificar precios de este producto (no es de tu línea)",
                incluir=False,
            )
            continue

        vinculo = vinculo_por_codigo_proveedor or ProductoProveedor.objects.filter(
            producto=producto, proveedor=importacion.proveedor
        ).first()

        if vinculo is None:
            categoria = ImportacionFila.Categoria.NUEVO_VINCULO
            detalle = ""
        else:
            historial = costo_actual(vinculo)
            if historial is not None and historial.costo == costo:
                categoria = ImportacionFila.Categoria.SIN_CAMBIOS
                detalle = ""
            else:
                categoria = ImportacionFila.Categoria.ACTUALIZA_COSTO
                detalle = f"${historial.costo} → ${costo}" if historial else f"Primer costo: ${costo}"

        if nombre.strip().lower() != producto.nombre.strip().lower():
            categoria = ImportacionFila.Categoria.PARA_REVISAR
            detalle = f"Nombre distinto al registrado: '{nombre}' vs '{producto.nombre}'"

        ImportacionFila.objects.create(
            **base, costo=costo, producto=producto, categoria=categoria, detalle=detalle,
            incluir=(categoria != ImportacionFila.Categoria.SIN_CAMBIOS),
        )


@transaction.atomic
def confirmar_importacion(importacion, usuario):
    """
    Aplica las filas marcadas incluir=True (salvo error). Vuelve a validar
    permisos acá, sin confiar en la categorización de cuando se subió el
    archivo: si algo cambió entre medio (o alguien tildó a mano una fila
    que no debería), esta es la barrera real, no la de la vista previa.
    """
    contadores = {"creados": 0, "actualizados": 0, "omitidos": 0}

    filas = importacion.filas.filter(incluir=True).exclude(
        categoria=ImportacionFila.Categoria.ERROR
    ).select_related("producto")

    for fila in filas:
        if fila.producto is None:
            if not puede_crear_producto(usuario):
                contadores["omitidos"] += 1
                continue

            marca = _buscar_marca(fila.marca_texto)
            if marca is None:
                if not usuario.has_perm("catalog.add_marca"):
                    contadores["omitidos"] += 1
                    continue
                marca = Marca.objects.create(nombre=fila.marca_texto)

            producto = Producto.objects.create(
                marca=marca,
                codigo=fila.codigo,
                nombre=fila.nombre_texto,
                es_repuesto=not usuario.has_perm("catalog.add_producto"),
            )
            vinculo = ProductoProveedor.objects.create(
                producto=producto,
                proveedor=importacion.proveedor,
                codigo_proveedor=fila.codigo_proveedor_texto,
            )
            registrar_costo(vinculo, fila.costo, usuario, origen=f"importación #{importacion.pk}")
            fila.producto = producto
            fila.save(update_fields=["producto"])
            contadores["creados"] += 1
            continue

        if not puede_registrar_costo(usuario, fila.producto):
            contadores["omitidos"] += 1
            continue

        vinculo, _ = ProductoProveedor.objects.get_or_create(
            producto=fila.producto,
            proveedor=importacion.proveedor,
            defaults={"codigo_proveedor": fila.codigo_proveedor_texto},
        )
        registrar_costo(vinculo, fila.costo, usuario, origen=f"importación #{importacion.pk}")
        contadores["actualizados"] += 1

    importacion.estado = ImportacionListaPrecios.Estado.CONFIRMADA
    importacion.confirmada_por = usuario
    importacion.confirmada_en = timezone.now()
    importacion.save(update_fields=["estado", "confirmada_por", "confirmada_en"])

    return contadores
