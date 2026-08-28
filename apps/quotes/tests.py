from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.clients.models import Cliente
from apps.pricing.models import ConfiguracionGeneral
from apps.pricing.services import registrar_costo
from apps.tasks.models import Tarea, TipoAutomatizacion

from .models import (
    EstadoPresupuesto,
    ItemPresupuesto,
    PlantillaCondiciones,
    Presupuesto,
    SeccionPresupuesto,
    TipoDescuento,
)
from .permissions import puede_revertir_aceptado
from .services import (
    TRANSICIONES_VALIDAS,
    TransicionInvalidaError,
    calcular_totales,
    cambiar_estado,
    duplicar_presupuesto,
    enviar_presupuesto,
    margen_item,
    sugerir_costo_mano_obra,
)


class PresupuestoNumeroSecuenciaTests(TestCase):
    """Regla de negocio: numero sale de una secuencia de Postgres, nunca de max()+1."""

    def test_numeros_son_unicos_y_correlativos(self):
        cliente = Cliente.objects.create(nombre="Cliente Secuencia")
        p1 = Presupuesto.objects.create(cliente=cliente)
        p2 = Presupuesto.objects.create(cliente=cliente)
        self.assertNotEqual(p1.numero, p2.numero)
        self.assertEqual(p2.numero, p1.numero + 1)


class PlantillaCondicionesConstraintTests(TestCase):
    def test_no_puede_haber_dos_predeterminadas(self):
        PlantillaCondiciones.objects.create(nombre="A", texto="...", predeterminada=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PlantillaCondiciones.objects.create(nombre="B", texto="...", predeterminada=True)

    def test_permite_varias_no_predeterminadas(self):
        PlantillaCondiciones.objects.create(nombre="A", texto="...", predeterminada=False)
        PlantillaCondiciones.objects.create(nombre="B", texto="...", predeterminada=False)
        self.assertEqual(PlantillaCondiciones.objects.count(), 2)


class ItemPresupuestoConstraintTests(TestCase):
    def test_no_puede_ser_no_opcional_y_no_incluido(self):
        cliente = Cliente.objects.create(nombre="Cliente Item")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    descripcion_manual="Item inválido",
                    precio_unitario=Decimal("100"),
                    opcional=False,
                    incluido=False,
                )

    def test_opcional_no_incluido_es_valido(self):
        cliente = Cliente.objects.create(nombre="Cliente Item 2")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        item = ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            descripcion_manual="Mejora opcional",
            precio_unitario=Decimal("100"),
            opcional=True,
            incluido=False,
        )
        self.assertFalse(item.incluido)

    def test_no_puede_tener_ni_producto_ni_descripcion(self):
        """
        Fix retroactivo (Etapa 8): esto quedó sin CheckConstraint en el
        diseño original, confiando en que la UI ofrece dos formularios
        separados (ItemCatalogoForm/ItemManualForm) — un INSERT directo
        (saltándose esos formularios) tampoco puede dejar la fila en un
        estado inválido, mismo patrón ya aplicado en MaterialTrabajo.
        """
        cliente = Cliente.objects.create(nombre="Cliente Item Sin Nada")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto, precio_unitario=Decimal("100")
                )

    def test_no_puede_tener_los_dos_a_la_vez(self):
        cliente = Cliente.objects.create(nombre="Cliente Item Con Ambos")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        marca = Marca.objects.create(nombre="Marca Constraint Item")
        producto = Producto.objects.create(marca=marca, codigo="CI-1", nombre="Producto Constraint")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    producto=producto,
                    descripcion_manual="También manual",
                    precio_unitario=Decimal("100"),
                )

    def test_solo_producto_es_valido(self):
        cliente = Cliente.objects.create(nombre="Cliente Item Solo Producto")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        marca = Marca.objects.create(nombre="Marca Constraint Item 2")
        producto = Producto.objects.create(marca=marca, codigo="CI-2", nombre="Producto Constraint 2")
        item = ItemPresupuesto.objects.create(
            presupuesto=presupuesto, producto=producto, precio_unitario=Decimal("100")
        )
        self.assertIsNotNone(item.pk)


class CalcularTotalesTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Totales")

    def test_descuento_porcentual_se_aplica_antes_de_multiplicar_unidades(self):
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente,
            cantidad_unidades=3,
            descuento_general_tipo=TipoDescuento.PORCENTAJE,
            descuento_general_valor=Decimal("10"),
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Item", cantidad=1,
            precio_unitario=Decimal("1000"),
        )
        totales = calcular_totales(presupuesto)
        # 1000 - 10% = 900; x3 unidades = 2700
        self.assertEqual(totales["total_final"], Decimal("2700.00"))

    def test_descuento_monto_fijo_se_resta_una_sola_vez(self):
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente,
            cantidad_unidades=3,
            descuento_general_tipo=TipoDescuento.MONTO,
            descuento_general_valor=Decimal("100000"),
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Item", cantidad=1,
            precio_unitario=Decimal("500000"),
        )
        totales = calcular_totales(presupuesto)
        # 500000 x3 = 1500000; -100000 UNA sola vez = 1400000 (no 300000 de descuento)
        self.assertEqual(totales["total_final"], Decimal("1400000.00"))

    def test_items_no_incluidos_no_suman(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Incluido", precio_unitario=Decimal("100"),
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Opcional no activado",
            precio_unitario=Decimal("500"), opcional=True, incluido=False,
        )
        totales = calcular_totales(presupuesto)
        self.assertEqual(totales["total_final"], Decimal("100.00"))


class MargenYEnvioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cliente = Cliente.objects.create(nombre="Cliente Margen")
        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        cls.rodrigo = User.objects.create_user(username="rodrigo_margen", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)

    def test_margen_item_es_none_sin_costo_cargado(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        item = ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Sin costo", precio_unitario=Decimal("100"),
        )
        self.assertIsNone(margen_item(item))

    def test_sugerir_costo_mano_obra_usa_margen_configurado(self):
        config = ConfiguracionGeneral.obtener()
        config.margen_mano_obra = Decimal("40.00")
        config.save()
        costo = sugerir_costo_mano_obra(Decimal("1400.00"))
        self.assertEqual(costo, Decimal("1000.00"))

    def test_enviar_con_margen_bajo_audita_y_no_bloquea(self):
        from apps.audit.models import AuditLog

        config = ConfiguracionGeneral.obtener()
        config.margen_minimo_alerta = Decimal("15.00")
        config.save()

        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Margen bajo",
            precio_unitario=Decimal("1000"), costo_unitario=Decimal("990"),
        )

        antes = AuditLog.objects.count()
        enviar_presupuesto(presupuesto, self.rodrigo)
        presupuesto.refresh_from_db()

        self.assertEqual(presupuesto.estado, EstadoPresupuesto.ENVIADO)
        self.assertEqual(AuditLog.objects.count(), antes + 1)
        log = AuditLog.objects.latest("id")
        self.assertEqual(log.accion, "enviar_presupuesto_margen_bajo")

    def test_reenvio_vuelve_a_auditar(self):
        from apps.audit.models import AuditLog

        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Item ok", precio_unitario=Decimal("100"),
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        antes = AuditLog.objects.count()

        cambiar_estado(presupuesto, EstadoPresupuesto.BORRADOR, self.rodrigo)
        enviar_presupuesto(presupuesto, self.rodrigo)

        self.assertGreater(AuditLog.objects.count(), antes)


class TransicionesEstadoTests(TestCase):
    """
    Cubre el grafo completo declarado en TRANSICIONES_VALIDAS: cada arista
    documentada debe funcionar, y una muestra representativa de pares NO
    declarados debe rechazarse.
    """

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Transiciones")
        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        self.diego = User.objects.create_user(username="diego_trans", password="clave12345")
        self.diego.groups.add(grupo_admin)

    def _presupuesto_en(self, estado):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        if estado != EstadoPresupuesto.BORRADOR:
            presupuesto.estado = EstadoPresupuesto.BORRADOR
            presupuesto.save(update_fields=["estado"])
            # atajo directo a la BD para no repasar todo el camino de
            # transiciones válidas al armar cada estado de partida
            Presupuesto.objects.filter(pk=presupuesto.pk).update(estado=estado)
            presupuesto.refresh_from_db()
        return presupuesto

    def test_todas_las_aristas_declaradas_son_validas(self):
        for origen, destinos in TRANSICIONES_VALIDAS.items():
            for destino in destinos:
                with self.subTest(origen=origen, destino=destino):
                    presupuesto = self._presupuesto_en(origen)
                    cambiar_estado(presupuesto, destino, self.diego)
                    presupuesto.refresh_from_db()
                    self.assertEqual(presupuesto.estado, destino)

    def test_transiciones_no_declaradas_se_rechazan(self):
        casos_invalidos = [
            (EstadoPresupuesto.BORRADOR, EstadoPresupuesto.ACEPTADO),
            (EstadoPresupuesto.BORRADOR, EstadoPresupuesto.RECHAZADO),
            (EstadoPresupuesto.RECHAZADO, EstadoPresupuesto.ACEPTADO),
            (EstadoPresupuesto.ACEPTADO, EstadoPresupuesto.BORRADOR),
            (EstadoPresupuesto.ACEPTADO, EstadoPresupuesto.ENVIADO),
            (EstadoPresupuesto.CANCELADO, EstadoPresupuesto.BORRADOR),
        ]
        for origen, destino in casos_invalidos:
            with self.subTest(origen=origen, destino=destino):
                presupuesto = self._presupuesto_en(origen)
                with self.assertRaises(TransicionInvalidaError):
                    cambiar_estado(presupuesto, destino, self.diego)
                presupuesto.refresh_from_db()
                self.assertEqual(presupuesto.estado, origen)

    def test_cancelado_no_tiene_transiciones_salientes(self):
        self.assertEqual(TRANSICIONES_VALIDAS[EstadoPresupuesto.CANCELADO], set())


class TriggerBloqueoEdicionTests(TestCase):
    """
    El trigger de Postgres (no una validación de Python) es el resguardo
    real de que Secciones/Ítems solo se editen con el presupuesto en
    Borrador — se prueba yendo directo por SQL, como en
    pricing.HistorialCostoInmutableTests.
    """

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Trigger")
        self.presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        self.item = ItemPresupuesto.objects.create(
            presupuesto=self.presupuesto, descripcion_manual="Item", precio_unitario=Decimal("100"),
        )
        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        self.diego = User.objects.create_user(username="diego_trigger", password="clave12345")
        self.diego.groups.add(grupo_admin)
        enviar_presupuesto(self.presupuesto, self.diego)

    def test_no_se_puede_insertar_item_fuera_de_borrador(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=self.presupuesto, descripcion_manual="Nuevo",
                    precio_unitario=Decimal("50"),
                )

    def test_no_se_puede_actualizar_item_fuera_de_borrador(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE quotes_itempresupuesto SET cantidad = 5 WHERE id = %s",
                        [self.item.id],
                    )

    def test_no_se_puede_borrar_item_fuera_de_borrador(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM quotes_itempresupuesto WHERE id = %s", [self.item.id]
                    )

    def test_se_puede_editar_de_nuevo_tras_reabrir_a_borrador(self):
        cambiar_estado(self.presupuesto, EstadoPresupuesto.BORRADOR, self.diego)
        self.item.cantidad = Decimal("5")
        self.item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.cantidad, Decimal("5"))


class DuplicarPresupuestoTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Duplicar")
        self.marca = Marca.objects.create(nombre="Marca Duplicar")
        self.proveedor = Proveedor.objects.create(nombre_comercial="Proveedor Duplicar")
        self.producto = Producto.objects.create(marca=self.marca, codigo="D1", nombre="Producto D")
        self.pp = ProductoProveedor.objects.create(producto=self.producto, proveedor=self.proveedor)
        registrar_costo(self.pp, Decimal("1000"), usuario=None)

        self.plantilla = PlantillaCondiciones.objects.create(
            nombre="Plantilla Duplicar", texto="Texto original", predeterminada=False
        )
        self.original = Presupuesto.objects.create(
            cliente=self.cliente, plantilla_condiciones=self.plantilla, condiciones=self.plantilla.texto
        )
        self.seccion = SeccionPresupuesto.objects.create(presupuesto=self.original, titulo="Etapa 1")
        self.item_catalogo = ItemPresupuesto.objects.create(
            presupuesto=self.original, seccion=self.seccion, producto=self.producto,
            producto_proveedor=self.pp, precio_unitario=Decimal("1300"), costo_unitario=Decimal("1000"),
        )
        self.item_manual = ItemPresupuesto.objects.create(
            presupuesto=self.original, descripcion_manual="Mano de obra", precio_unitario=Decimal("500"),
        )

    def test_nace_en_borrador_con_numero_nuevo(self):
        nuevo = duplicar_presupuesto(self.original, usuario=None)
        self.assertEqual(nuevo.estado, EstadoPresupuesto.BORRADOR)
        self.assertNotEqual(nuevo.numero, self.original.numero)

    def test_recalcula_costo_de_catalogo_desde_el_mismo_proveedor(self):
        registrar_costo(self.pp, Decimal("1200"), usuario=None)  # el costo cambió
        self.plantilla.texto = "Texto actualizado"
        self.plantilla.save()

        nuevo = duplicar_presupuesto(self.original, usuario=None)

        item_nuevo = nuevo.items.get(producto=self.producto)
        self.assertEqual(item_nuevo.costo_unitario, Decimal("1200"))
        self.assertNotEqual(item_nuevo.precio_unitario, self.item_catalogo.precio_unitario)
        self.assertEqual(nuevo.condiciones, "Texto actualizado")
        self.assertEqual(nuevo.plantilla_condiciones, self.plantilla)

    def test_recalcula_costo_manual_via_margen_mano_obra(self):
        nuevo = duplicar_presupuesto(self.original, usuario=None)
        item_nuevo = nuevo.items.get(producto__isnull=True)
        self.assertEqual(item_nuevo.precio_unitario, self.item_manual.precio_unitario)
        self.assertEqual(item_nuevo.costo_unitario, sugerir_costo_mano_obra(self.item_manual.precio_unitario))

    def test_clona_secciones_y_reasigna_items(self):
        nuevo = duplicar_presupuesto(self.original, usuario=None)
        self.assertEqual(nuevo.secciones.count(), 1)
        seccion_nueva = nuevo.secciones.first()
        self.assertEqual(seccion_nueva.titulo, "Etapa 1")
        self.assertEqual(nuevo.items.get(producto=self.producto).seccion, seccion_nueva)

    def test_mantiene_valores_congelados_si_no_hay_costo_para_ese_proveedor(self):
        pp_sin_costo = ProductoProveedor.objects.create(
            producto=self.producto, proveedor=Proveedor.objects.create(nombre_comercial="Otro Prov")
        )
        item_sin_costo = ItemPresupuesto.objects.create(
            presupuesto=self.original, producto=self.producto, producto_proveedor=pp_sin_costo,
            precio_unitario=Decimal("999"), costo_unitario=Decimal("777"),
        )
        nuevo = duplicar_presupuesto(self.original, usuario=None)
        item_nuevo = nuevo.items.get(producto_proveedor=pp_sin_costo)
        self.assertEqual(item_nuevo.precio_unitario, item_sin_costo.precio_unitario)
        self.assertEqual(item_nuevo.costo_unitario, item_sin_costo.costo_unitario)


class VencerPresupuestosCommandTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Vencido")
        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        self.diego = User.objects.create_user(username="diego_vencido", password="clave12345")
        self.diego.groups.add(grupo_admin)

    def _presupuesto_enviado(self, fecha_vencimiento):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente, fecha_vencimiento=fecha_vencimiento)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.diego)
        return presupuesto

    def test_marca_vencido_solo_el_que_ya_paso_de_fecha(self):
        hoy = timezone.localdate()
        vencido = self._presupuesto_enviado(hoy - timezone.timedelta(days=1))
        vigente = self._presupuesto_enviado(hoy + timezone.timedelta(days=1))

        call_command("vencer_presupuestos")

        vencido.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual(vencido.estado, EstadoPresupuesto.VENCIDO)
        self.assertEqual(vigente.estado, EstadoPresupuesto.ENVIADO)

    def test_es_idempotente(self):
        hoy = timezone.localdate()
        vencido = self._presupuesto_enviado(hoy - timezone.timedelta(days=1))

        call_command("vencer_presupuestos")
        call_command("vencer_presupuestos")

        vencido.refresh_from_db()
        self.assertEqual(vencido.estado, EstadoPresupuesto.VENCIDO)


class GenerarSeguimientoPresupuestosCommandTests(TestCase):
    """
    No hay un campo fecha_envio en Presupuesto: la referencia es el
    AuditLog de enviar_presupuesto(), por eso estos tests lo backdatean
    directo con .update() (auto_now_add no se puede setear al crear).
    """

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Seguimiento")
        self.rodrigo = User.objects.create_user(username="rodrigo_seguimiento", password="clave12345")

    def _presupuesto_enviado_hace(self, dias):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente, creado_por=self.rodrigo)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        self._backdatear_ultimo_envio(presupuesto, dias)
        return presupuesto

    def _backdatear_ultimo_envio(self, presupuesto, dias):
        content_type = ContentType.objects.get_for_model(Presupuesto)
        ultimo = (
            AuditLog.objects.filter(
                content_type=content_type, object_id=str(presupuesto.pk), accion="enviar_presupuesto",
            )
            .order_by("-creado_en")
            .first()
        )
        AuditLog.objects.filter(pk=ultimo.pk).update(
            creado_en=timezone.now() - timezone.timedelta(days=dias)
        )

    def test_genera_tarea_tras_el_umbral_configurado(self):
        presupuesto = self._presupuesto_enviado_hace(3)
        call_command("generar_seguimiento_presupuestos")
        tarea = Tarea.objects.get(presupuesto=presupuesto)
        self.assertEqual(tarea.generada_por, TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO)
        self.assertEqual(tarea.asignado_a, self.rodrigo)
        self.assertIsNone(tarea.asignado_por)

    def test_no_genera_antes_del_umbral(self):
        self._presupuesto_enviado_hace(2)
        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.count(), 0)

    def test_es_idempotente(self):
        self._presupuesto_enviado_hace(3)
        call_command("generar_seguimiento_presupuestos")
        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.count(), 1)

    def test_reenvio_reabre_la_ventana(self):
        presupuesto = self._presupuesto_enviado_hace(3)
        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.count(), 1)
        # La primera tarea se creó en tiempo real ("ahora"): para que la
        # cronología sea consistente, la corremos hacia atrás también a
        # ella, como si hubiese pasado hace 6 días (antes del reenvío).
        Tarea.objects.filter(presupuesto=presupuesto).update(
            creado_en=timezone.now() - timezone.timedelta(days=6)
        )

        cambiar_estado(presupuesto, EstadoPresupuesto.BORRADOR, self.rodrigo)
        enviar_presupuesto(presupuesto, self.rodrigo)
        self._backdatear_ultimo_envio(presupuesto, 3)

        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.filter(presupuesto=presupuesto).count(), 2)

    def test_respeta_configuracion_general(self):
        config = ConfiguracionGeneral.obtener()
        config.dias_seguimiento_presupuesto_enviado = 5
        config.save()

        self._presupuesto_enviado_hace(3)
        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.count(), 0)

    def test_no_genera_para_presupuesto_sin_enviar(self):
        Presupuesto.objects.create(cliente=self.cliente, creado_por=self.rodrigo)
        call_command("generar_seguimiento_presupuestos")
        self.assertEqual(Tarea.objects.count(), 0)


class AvisarPresupuestosPorVencerCommandTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre="Cliente Por Vencer")
        self.rodrigo = User.objects.create_user(username="rodrigo_porvencer", password="clave12345")

    def _presupuesto_enviado(self, fecha_vencimiento):
        presupuesto = Presupuesto.objects.create(
            cliente=self.cliente, creado_por=self.rodrigo, fecha_vencimiento=fecha_vencimiento,
        )
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        return presupuesto

    def test_avisa_dentro_del_umbral(self):
        hoy = timezone.localdate()
        presupuesto = self._presupuesto_enviado(hoy + timezone.timedelta(days=2))
        call_command("avisar_presupuestos_por_vencer")
        tarea = Tarea.objects.get(presupuesto=presupuesto)
        self.assertEqual(tarea.generada_por, TipoAutomatizacion.PRESUPUESTO_POR_VENCER)
        self.assertEqual(tarea.asignado_a, self.rodrigo)

    def test_no_avisa_fuera_del_umbral(self):
        hoy = timezone.localdate()
        self._presupuesto_enviado(hoy + timezone.timedelta(days=10))
        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.count(), 0)

    def test_no_avisa_de_uno_ya_vencido(self):
        hoy = timezone.localdate()
        self._presupuesto_enviado(hoy - timezone.timedelta(days=1))
        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.count(), 0)

    def test_es_idempotente_sin_reenvio(self):
        hoy = timezone.localdate()
        self._presupuesto_enviado(hoy + timezone.timedelta(days=1))
        call_command("avisar_presupuestos_por_vencer")
        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.count(), 1)

    def test_reenvio_con_nueva_fecha_reabre_el_aviso(self):
        # Reabrir, cambiar fecha_vencimiento y reenviar es parte normal
        # del ciclo de vida de Presupuesto (Etapa 5) — un reenvío tiene
        # que volver a habilitar el aviso, mismo criterio que seguimiento.
        hoy = timezone.localdate()
        presupuesto = self._presupuesto_enviado(hoy + timezone.timedelta(days=1))
        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.filter(presupuesto=presupuesto).count(), 1)

        cambiar_estado(presupuesto, EstadoPresupuesto.BORRADOR, self.rodrigo)
        presupuesto.fecha_vencimiento = hoy + timezone.timedelta(days=2)
        presupuesto.save()
        enviar_presupuesto(presupuesto, self.rodrigo)

        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.filter(presupuesto=presupuesto).count(), 2)

    def test_respeta_configuracion_general(self):
        config = ConfiguracionGeneral.obtener()
        config.dias_aviso_presupuesto_por_vencer = 1
        config.save()

        hoy = timezone.localdate()
        self._presupuesto_enviado(hoy + timezone.timedelta(days=2))
        call_command("avisar_presupuestos_por_vencer")
        self.assertEqual(Tarea.objects.count(), 0)


class RevertirAceptadoPermisosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cliente = Cliente.objects.create(nombre="Cliente Revertir")

        grupo_admin, _ = Group.objects.get_or_create(name="Administrador")
        permiso = Permission.objects.get(
            codename="revert_presupuesto_aceptado", content_type__app_label="quotes"
        )
        grupo_admin.permissions.add(permiso)
        for codename in ("view_presupuesto", "change_presupuesto"):
            grupo_admin.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="quotes")
            )

        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        for codename in ("view_presupuesto", "change_presupuesto", "add_itempresupuesto"):
            grupo_ventas.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="quotes")
            )

        cls.diego = User.objects.create_user(username="diego_revertir", password="clave12345")
        cls.diego.groups.add(grupo_admin)
        cls.rodrigo = User.objects.create_user(username="rodrigo_revertir", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)

    def _presupuesto_aceptado(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.rodrigo)
        cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.rodrigo)
        return presupuesto

    def test_puede_revertir_aceptado_helper(self):
        presupuesto = self._presupuesto_aceptado()
        self.assertTrue(puede_revertir_aceptado(self.diego, presupuesto))
        self.assertFalse(puede_revertir_aceptado(self.rodrigo, presupuesto))

    def test_ni_diego_puede_revertir_si_ya_existe_un_trabajo(self):
        from apps.jobs.services import crear_trabajo

        presupuesto = self._presupuesto_aceptado()
        crear_trabajo(presupuesto, self.diego)
        self.assertFalse(puede_revertir_aceptado(self.diego, presupuesto))

        self.client.login(username="diego_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:revertir_aceptado", args=[presupuesto.pk]))
        self.assertEqual(response.status_code, 403)
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.ACEPTADO)

    def test_diego_si_puede_revertir_si_el_trabajo_ya_esta_cancelado(self):
        """
        Si la obra se cayó y el Trabajo ya está Cancelado, no hay razón
        para seguir bloqueando la reversión del presupuesto que le dio
        origen — al contrario, tiene sentido poder deshacer también la
        aceptación.
        """
        from apps.jobs.models import EstadoTrabajo
        from apps.jobs.services import cancelar_trabajo, crear_trabajo

        presupuesto = self._presupuesto_aceptado()
        trabajo = crear_trabajo(presupuesto, self.diego)
        cancelar_trabajo(trabajo, self.diego, motivo="El cliente se bajó")

        self.assertTrue(puede_revertir_aceptado(self.diego, presupuesto))

        self.client.login(username="diego_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:revertir_aceptado", args=[presupuesto.pk]))
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.CANCELADO)
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, EstadoTrabajo.CANCELADO)

    def test_rodrigo_no_puede_revertir_via_vista(self):
        presupuesto = self._presupuesto_aceptado()
        self.client.login(username="rodrigo_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:revertir_aceptado", args=[presupuesto.pk]))
        self.assertEqual(response.status_code, 403)
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.ACEPTADO)

    def test_diego_puede_revertir_via_vista(self):
        presupuesto = self._presupuesto_aceptado()
        self.client.login(username="diego_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:revertir_aceptado", args=[presupuesto.pk]))
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.CANCELADO)

    def test_rodrigo_no_puede_esquivar_el_permiso_via_cancelar_generico(self):
        """
        Regresión: Aceptado→Cancelado está en TRANSICIONES_VALIDAS (es la
        misma transición que usa RevertirAceptadoView), así que sin este
        chequeo de estado en CancelarPresupuestoView, cualquiera con
        change_presupuesto (Rodrigo incluido) podía cancelar un Aceptado
        pegándole directo a /cancelar/, sin tener revert_presupuesto_aceptado.
        """
        presupuesto = self._presupuesto_aceptado()
        self.client.login(username="rodrigo_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:cancelar", args=[presupuesto.pk]))
        self.assertEqual(response.status_code, 403)
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.ACEPTADO)

    def test_rodrigo_si_puede_cancelar_un_enviado_via_cancelar_generico(self):
        """El fix de arriba no debe romper el uso normal de /cancelar/."""
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="X", precio_unitario=Decimal("100")
        )
        enviar_presupuesto(presupuesto, self.rodrigo)

        self.client.login(username="rodrigo_revertir", password="clave12345")
        response = self.client.post(reverse("quotes:cancelar", args=[presupuesto.pk]))
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        presupuesto.refresh_from_db()
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.CANCELADO)


class PresupuestoViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cliente = Cliente.objects.create(nombre="Cliente Vistas")

        grupo_ventas, _ = Group.objects.get_or_create(name="Ventas y Presupuestos")
        for codename in (
            "view_presupuesto", "add_presupuesto", "change_presupuesto",
            "add_itempresupuesto", "delete_itempresupuesto",
            "add_seccionpresupuesto", "delete_seccionpresupuesto",
        ):
            grupo_ventas.permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="quotes")
            )

        grupo_tecnico, _ = Group.objects.get_or_create(name="Técnico de Campo")

        cls.rodrigo = User.objects.create_user(username="rodrigo_vistas", password="clave12345")
        cls.rodrigo.groups.add(grupo_ventas)
        cls.andres = User.objects.create_user(username="andres_vistas", password="clave12345")
        cls.andres.groups.add(grupo_tecnico)

    def test_andres_no_puede_ver_presupuestos(self):
        self.client.login(username="andres_vistas", password="clave12345")
        response = self.client.get(reverse("quotes:lista"))
        self.assertEqual(response.status_code, 403)

    def test_rodrigo_puede_crear_presupuesto(self):
        self.client.login(username="rodrigo_vistas", password="clave12345")
        response = self.client.post(
            reverse("quotes:nuevo"),
            {
                "cliente": self.cliente.pk, "direccion": "Calle 123", "fecha_vencimiento": "",
                "cantidad_unidades": 1, "descuento_general_tipo": "porcentaje",
                "descuento_general_valor": 0, "notas_generales": "", "plantilla_condiciones": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        presupuesto = Presupuesto.objects.get(cliente=self.cliente)
        self.assertEqual(presupuesto.creado_por, self.rodrigo)
        self.assertEqual(presupuesto.estado, EstadoPresupuesto.BORRADOR)

    def test_agregar_item_manual_solo_en_borrador(self):
        self.client.login(username="rodrigo_vistas", password="clave12345")
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)

        response = self.client.post(
            reverse("quotes:agregar_item_manual", args=[presupuesto.pk]),
            {
                "seccion": "", "descripcion_manual": "Instalación", "cantidad": 1,
                "precio_unitario": "1000.00", "costo_unitario": "", "descuento_pct": 0,
                "tipo_iva": "incluido", "incluido": "on",
            },
        )
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        self.assertTrue(presupuesto.items.filter(descripcion_manual="Instalación").exists())

        enviar_presupuesto(presupuesto, self.rodrigo)
        response = self.client.post(
            reverse("quotes:agregar_item_manual", args=[presupuesto.pk]),
            {
                "seccion": "", "descripcion_manual": "No debería entrar", "cantidad": 1,
                "precio_unitario": "1.00", "costo_unitario": "", "descuento_pct": 0,
                "tipo_iva": "incluido", "incluido": "on",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_eliminar_seccion_con_items_no_borra(self):
        self.client.login(username="rodrigo_vistas", password="clave12345")
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        seccion = SeccionPresupuesto.objects.create(presupuesto=presupuesto, titulo="Etapa 1")
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, seccion=seccion, descripcion_manual="Item",
            precio_unitario=Decimal("100"),
        )

        response = self.client.post(reverse("quotes:eliminar_seccion", args=[seccion.pk]))
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        self.assertTrue(SeccionPresupuesto.objects.filter(pk=seccion.pk).exists())

    def test_exportar_pdf(self):
        self.client.login(username="rodrigo_vistas", password="clave12345")
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto, descripcion_manual="Item", precio_unitario=Decimal("100")
        )
        response = self.client.get(reverse("quotes:pdf", args=[presupuesto.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
