import tempfile
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.pricing.services import costo_actual, registrar_costo
from apps.stock.models import Deposito
from apps.stock.services import stock_actual

from .models import EstadoOrdenCompra, LineaOrdenCompra, OrdenDeCompra
from .permissions import puede_cancelar_orden, puede_cerrar_orden, puede_gestionar_orden
from .services import (
    TransicionInvalidaError,
    cambiar_estado_orden,
    cantidad_pendiente_recepcion,
    cantidad_recibida,
    cerrar_orden,
    crear_orden,
    recibir_linea,
)


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


def _proveedor(nombre="Proveedor Test", email=""):
    return Proveedor.objects.create(nombre_comercial=nombre, email=email)


def _producto_proveedor(proveedor, codigo="COD-1"):
    marca = Marca.objects.create(nombre=f"Marca {codigo}")
    producto = Producto.objects.create(
        marca=marca,
        codigo=codigo,
        nombre=f"Producto {codigo}",
    )
    return ProductoProveedor.objects.create(producto=producto, proveedor=proveedor)


def _agregar_linea(orden, pp, cantidad="1", costo="100"):
    return LineaOrdenCompra.objects.create(
        orden=orden,
        producto_proveedor=pp,
        cantidad=Decimal(cantidad),
        costo_esperado=Decimal(costo),
    )


def _emitir_y_enviar(orden, usuario):
    cambiar_estado_orden(orden, EstadoOrdenCompra.EMITIDA, usuario)
    cambiar_estado_orden(orden, EstadoOrdenCompra.ENVIADA, usuario)
    orden.refresh_from_db()
    return orden


class ModelConstraintTests(TestCase):
    """Garantías que viven en triggers de Postgres, no solo en la UI."""

    def setUp(self):
        self.diego = _crear_usuario("diego_constraints", "Administrador")
        self.prov_a = _proveedor("Proveedor A")
        self.prov_b = _proveedor("Proveedor B")
        self.pp_a = _producto_proveedor(self.prov_a, "A-1")
        self.pp_b = _producto_proveedor(self.prov_b, "B-1")
        self.orden = crear_orden(self.prov_a, Deposito.GENERAL, self.diego)

    def test_trigger_rechaza_linea_de_otro_proveedor(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                _agregar_linea(self.orden, self.pp_b)

    def test_trigger_permite_linea_del_mismo_proveedor(self):
        linea = _agregar_linea(self.orden, self.pp_a)
        self.assertEqual(linea.orden, self.orden)

    def test_trigger_rechaza_update_a_producto_de_otro_proveedor(self):
        linea = _agregar_linea(self.orden, self.pp_a)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.producto_proveedor = self.pp_b
                linea.save()

    def test_emitir_congela_edicion_de_lineas(self):
        linea = _agregar_linea(self.orden, self.pp_a)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.EMITIDA, self.diego)

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.cantidad = Decimal("2")
                linea.save()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                _agregar_linea(self.orden, self.pp_a)

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                linea.delete()

    def test_trigger_bloquea_salto_directo_borrador_a_enviada(self):
        _agregar_linea(self.orden, self.pp_a)
        self.orden.estado = EstadoOrdenCompra.ENVIADA
        self.orden.enviada_por = self.diego
        from django.utils import timezone
        self.orden.enviada_en = timezone.now()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                self.orden.save(update_fields=["estado", "enviada_por", "enviada_en"])

    def test_permite_editar_linea_al_reabrir_emitida(self):
        linea = _agregar_linea(self.orden, self.pp_a)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.EMITIDA, self.diego)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)
        linea.cantidad = Decimal("3")
        linea.save()
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad, Decimal("3"))

    def test_numero_sale_de_secuencia_postgres(self):
        otra = crear_orden(self.prov_a, Deposito.GENERAL, self.diego)
        self.assertGreater(otra.numero, self.orden.numero)
        with connection.cursor() as cur:
            cur.execute("SELECT pg_get_serial_sequence('purchasing_ordendecompra', 'numero')")
            self.assertIsNotNone(cur.fetchone()[0])


class TransicionesEstadoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_transiciones", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_transiciones", "Ventas y Presupuestos")
        self.proveedor = _proveedor()
        self.pp = _producto_proveedor(self.proveedor, "TR-1")
        self.orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        _agregar_linea(self.orden, self.pp, cantidad="2", costo="50")

    def test_flujo_borrador_emitida_enviada_cancelada(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.EMITIDA, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.EMITIDA)
        self.assertEqual(self.orden.emitida_por, self.diego)
        self.assertIsNotNone(self.orden.emitida_en)

        cambiar_estado_orden(self.orden, EstadoOrdenCompra.ENVIADA, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.ENVIADA)

        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.CANCELADA,
            self.diego,
            motivo="Compra cancelada",
        )
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.CANCELADA)

    def test_emitida_puede_volver_a_borrador_y_limpia_emision_vigente(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.EMITIDA, self.rodrigo)
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.rodrigo)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.BORRADOR)
        self.assertIsNone(self.orden.emitida_por)
        self.assertIsNone(self.orden.emitida_en)

    def test_no_se_puede_emitir_sin_lineas(self):
        vacia = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        with self.assertRaisesMessage(ValueError, "sin líneas"):
            cambiar_estado_orden(vacia, EstadoOrdenCompra.EMITIDA, self.diego)

    def test_no_se_puede_saltar_de_borrador_a_enviada(self):
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.ENVIADA, self.diego)

    def test_no_se_puede_volver_de_enviada_a_borrador(self):
        _emitir_y_enviar(self.orden, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)

    def test_cancelada_es_terminal(self):
        cambiar_estado_orden(self.orden, EstadoOrdenCompra.EMITIDA, self.diego)
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.CANCELADA,
            self.diego,
            motivo="Cancelación de prueba",
        )
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_orden(self.orden, EstadoOrdenCompra.BORRADOR, self.diego)

    def test_rodrigo_puede_emitir_y_enviar_sin_aprobacion(self):
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        _agregar_linea(orden, self.pp, costo="50")
        cambiar_estado_orden(orden, EstadoOrdenCompra.EMITIDA, self.rodrigo)
        cambiar_estado_orden(orden, EstadoOrdenCompra.ENVIADA, self.rodrigo)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.ENVIADA)


class RecibirLineaTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_recibir", "Administrador")
        self.proveedor = _proveedor()
        self.pp = _producto_proveedor(self.proveedor)
        self.orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        self.linea = _agregar_linea(
            self.orden,
            self.pp,
            cantidad="10",
            costo="50",
        )
        _emitir_y_enviar(self.orden, self.diego)

    def test_no_se_puede_recibir_orden_en_borrador(self):
        otra = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = _agregar_linea(otra, self.pp, cantidad="5", costo="50")
        with self.assertRaises(ValueError):
            recibir_linea(linea, Decimal("1"), Decimal("50"), self.diego)

    def test_no_se_puede_recibir_orden_solo_emitida(self):
        otra = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = _agregar_linea(otra, self.pp, cantidad="2", costo="50")
        cambiar_estado_orden(otra, EstadoOrdenCompra.EMITIDA, self.diego)
        with self.assertRaisesMessage(ValueError, "Enviada"):
            recibir_linea(linea, Decimal("1"), Decimal("50"), self.diego)

    def test_cantidad_debe_ser_mayor_a_cero(self):
        with self.assertRaises(ValueError):
            recibir_linea(self.linea, Decimal("0"), Decimal("50"), self.diego)

    def test_rechaza_recibir_mas_de_lo_pendiente(self):
        with self.assertRaises(ValueError):
            recibir_linea(self.linea, Decimal("11"), Decimal("50"), self.diego)
        self.assertEqual(cantidad_recibida(self.linea), Decimal("0"))

    def test_recepcion_completa_actualiza_stock_costo_y_estado(self):
        recibir_linea(self.linea, Decimal("10"), Decimal("55"), self.diego)
        self.orden.refresh_from_db()

        self.assertEqual(cantidad_recibida(self.linea), Decimal("10"))
        self.assertEqual(cantidad_pendiente_recepcion(self.linea), Decimal("0"))
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("10"))
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.RECIBIDA)

        historial = costo_actual(self.pp)
        self.assertEqual(historial.costo, Decimal("55"))
        self.assertEqual(historial.origen, "orden_compra")

    def test_recepcion_parcial_dos_veces(self):
        recibir_linea(self.linea, Decimal("6"), Decimal("50"), self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.RECEPCION_PARCIAL)
        self.assertEqual(cantidad_pendiente_recepcion(self.linea), Decimal("4"))

        recibir_linea(self.linea, Decimal("4"), Decimal("52"), self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.RECIBIDA)
        self.assertEqual(cantidad_recibida(self.linea), Decimal("10"))

    def test_movimiento_queda_vinculado_a_orden_y_linea(self):
        movimiento = recibir_linea(self.linea, Decimal("3"), Decimal("50"), self.diego)
        self.assertEqual(movimiento.orden_compra, self.orden)
        self.assertEqual(movimiento.linea_orden_compra, self.linea)

    def test_cierre_parcial_exige_motivo(self):
        recibir_linea(self.linea, Decimal("3"), Decimal("50"), self.diego)
        with self.assertRaisesMessage(ValueError, "Debe indicar"):
            cerrar_orden(self.orden, self.diego)

    def test_cierre_completo_no_exige_motivo(self):
        recibir_linea(self.linea, Decimal("10"), Decimal("50"), self.diego)
        cerrar_orden(self.orden, self.diego)
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.CERRADA)


class PermisosTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_permisos", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_permisos", "Ventas y Presupuestos")
        self.gabriel = _crear_usuario("gabriel_permisos", "Service y Repuestos")
        self.andres = _crear_usuario("andres_permisos", "Técnico de Campo")
        self.contri = _crear_usuario("contri_permisos", "Depósito")

    def test_permiso_obsoleto_de_aprobacion_ya_no_existe(self):
        self.assertFalse(
            Permission.objects.filter(
                content_type__app_label="purchasing",
                codename="approve_ordendecompra",
            ).exists()
        )

    def test_rodrigo_gabriel_andres_diego_pueden_gestionar(self):
        for user in (self.diego, self.rodrigo, self.gabriel, self.andres):
            with self.subTest(user=user.username):
                self.assertTrue(puede_gestionar_orden(user))
        self.assertFalse(puede_gestionar_orden(self.contri))

    def test_solo_direccion_puede_cancelar_y_cerrar(self):
        self.assertTrue(puede_cancelar_orden(self.diego))
        self.assertTrue(puede_cerrar_orden(self.diego))
        for user in (self.rodrigo, self.gabriel, self.andres, self.contri):
            with self.subTest(user=user.username):
                self.assertFalse(puede_cancelar_orden(user))
                self.assertFalse(puede_cerrar_orden(user))


class ViewsTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_views", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_views", "Ventas y Presupuestos")
        self.contri = _crear_usuario("contri_views", "Depósito")
        self.proveedor = _proveedor()
        self.pp = _producto_proveedor(self.proveedor)
        registrar_costo(self.pp, Decimal("80"), self.diego)

    def test_lista_requiere_login(self):
        response = self.client.get(reverse("purchasing:lista"))
        self.assertEqual(response.status_code, 403)

    def test_contri_no_puede_crear_orden(self):
        self.client.login(username="contri_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:nueva"),
            {
                "proveedor": self.proveedor.pk,
                "deposito_destino": Deposito.GENERAL,
                "notas": "",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_rodrigo_crea_orden(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:nueva"),
            {
                "proveedor": self.proveedor.pk,
                "deposito_destino": Deposito.GENERAL,
                "notas": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrdenDeCompra.objects.filter(proveedor=self.proveedor).exists())

    def test_agregar_linea_prefill_costo_sugerido(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        response = self.client.get(
            reverse("purchasing:agregar_linea", args=[orden.pk]),
            {"producto_proveedor": self.pp.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].initial.get("costo_esperado"),
            Decimal("80"),
        )

    def test_rodrigo_emite_y_envia_por_email_sin_aprobacion(self):
        self.proveedor.email = "compras@proveedor.test"
        self.proveedor.save(update_fields=["email"])
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        _agregar_linea(orden, self.pp, costo="80")

        response = self.client.post(reverse("purchasing:emitir", args=[orden.pk]))
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.EMITIDA)

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                MEDIA_ROOT=media_root,
            ):
                response = self.client.post(
                    reverse("purchasing:enviar_email", args=[orden.pk])
                )

        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.ENVIADA)
        self.assertEqual(orden.enviada_a, "compras@proveedor.test")
        self.assertTrue(orden.pdf_generado.name)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["compras@proveedor.test"])
        self.assertTrue(
            any(
                attachment[2] == "application/pdf"
                for attachment in mail.outbox[0].attachments
            )
        )

    def test_emitir_sin_lineas_muestra_error_y_no_rompe(self):
        self.client.login(username="diego_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        response = self.client.post(reverse("purchasing:emitir", args=[orden.pk]))
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, EstadoOrdenCompra.BORRADOR)

    def test_rutas_de_aprobacion_desaparecieron(self):
        for nombre in ("aprobar", "rechazar", "enviar_a_aprobacion", "marcar_enviada"):
            with self.subTest(nombre=nombre):
                with self.assertRaises(NoReverseMatch):
                    reverse(f"purchasing:{nombre}", args=[1])

    def test_detalle_no_muestra_acciones_de_aprobacion(self):
        self.client.login(username="diego_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        _agregar_linea(orden, self.pp, costo="80")
        response = self.client.get(reverse("purchasing:detalle", args=[orden.pk]))
        self.assertContains(response, "Emitir orden")
        self.assertNotContains(response, "Enviar a aprobación")
        self.assertNotContains(response, ">Aprobar<")
        self.assertNotContains(response, ">Rechazar<")

    def test_rodrigo_no_puede_cancelar(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        _agregar_linea(orden, self.pp, costo="80")
        cambiar_estado_orden(orden, EstadoOrdenCompra.EMITIDA, self.rodrigo)
        response = self.client.post(
            reverse("purchasing:cancelar", args=[orden.pk]),
            {"motivo": "No comprar"},
        )
        self.assertEqual(response.status_code, 403)

    def test_contri_no_puede_recibir_deposito_ajeno(self):
        orden = crear_orden(self.proveedor, Deposito.REPUESTOS, self.diego)
        linea = _agregar_linea(orden, self.pp, cantidad="5", costo="80")
        _emitir_y_enviar(orden, self.diego)

        self.client.login(username="contri_views", password="clave12345")
        response = self.client.get(reverse("purchasing:recibir_linea", args=[linea.pk]))
        self.assertEqual(response.status_code, 403)

    def test_contri_recibe_en_stock_general(self):
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = _agregar_linea(orden, self.pp, cantidad="5", costo="80")
        _emitir_y_enviar(orden, self.diego)

        self.client.login(username="contri_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:recibir_linea", args=[linea.pk]),
            {"cantidad": "5", "costo_real": "82"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("5"))

    def test_no_se_puede_recibir_mas_de_lo_pendiente(self):
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.diego)
        linea = _agregar_linea(orden, self.pp, cantidad="5", costo="80")
        _emitir_y_enviar(orden, self.diego)

        self.client.login(username="diego_views", password="clave12345")
        response = self.client.post(
            reverse("purchasing:recibir_linea", args=[linea.pk]),
            {"cantidad": "6", "costo_real": "80"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No puede superar lo pendiente")
        self.assertEqual(stock_actual(self.pp.producto, Deposito.GENERAL), Decimal("0"))

    def test_eliminar_linea_solo_en_borrador(self):
        self.client.login(username="rodrigo_views", password="clave12345")
        orden = crear_orden(self.proveedor, Deposito.GENERAL, self.rodrigo)
        linea = _agregar_linea(orden, self.pp, cantidad="5", costo="80")
        cambiar_estado_orden(orden, EstadoOrdenCompra.EMITIDA, self.rodrigo)

        response = self.client.post(reverse("purchasing:eliminar_linea", args=[linea.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(LineaOrdenCompra.objects.filter(pk=linea.pk).exists())


class BuscadorProductosOrdenTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_buscador_oc", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_buscador_oc", "Ventas y Presupuestos")
        self.proveedor = _proveedor("Proveedor Buscador")
        self.otro_proveedor = _proveedor("Proveedor Ajeno")
        self.pp = _producto_proveedor(self.proveedor, "VAL-100")
        self.pp.producto.nombre = "Válvula esférica 3/4"
        self.pp.producto.save(update_fields=["nombre"])
        self.pp.codigo_proveedor = "PROV-7788"
        self.pp.save(update_fields=["codigo_proveedor"])
        registrar_costo(self.pp, Decimal("120.50"), self.diego)
        self.orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            self.rodrigo,
        )

    def _buscar(self, termino):
        return self.client.get(
            reverse("purchasing:buscar_productos", args=[self.orden.pk]),
            {"q": termino},
        )

    def test_formulario_usa_buscador_en_lugar_de_select_masivo(self):
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self.client.get(
            reverse("purchasing:agregar_linea", args=[self.orden.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="hidden" name="producto_proveedor"')
        self.assertNotContains(response, '<select name="producto_proveedor"')
        self.assertContains(response, "Buscar por código o nombre")
        self.assertContains(
            response,
            reverse("purchasing:buscar_productos", args=[self.orden.pk]),
        )

    def test_busca_por_codigo_nombre_y_codigo_del_proveedor(self):
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        for termino in ("VAL-100", "Válvula esférica", "PROV-7788"):
            with self.subTest(termino=termino):
                response = self._buscar(termino)
                self.assertEqual(response.status_code, 200)
                ids = [fila["id"] for fila in response.json()["resultados"]]
                self.assertIn(self.pp.pk, ids)

    def test_busqueda_solo_devuelve_productos_del_proveedor_de_la_orden(self):
        pp_ajeno = _producto_proveedor(self.otro_proveedor, "AJENO-01")
        pp_ajeno.producto.nombre = "Producto Ajeno Buscable"
        pp_ajeno.producto.save(update_fields=["nombre"])
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self._buscar("Ajeno")
        ids = [fila["id"] for fila in response.json()["resultados"]]
        self.assertNotIn(pp_ajeno.pk, ids)

    def test_busqueda_excluye_inactivos(self):
        pp_inactivo = _producto_proveedor(self.proveedor, "INACT-PP")
        pp_inactivo.producto.nombre = "Inactivo Relación"
        pp_inactivo.producto.save(update_fields=["nombre"])
        pp_inactivo.activo = False
        pp_inactivo.save(update_fields=["activo"])

        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self._buscar("Inactivo")
        ids = [fila["id"] for fila in response.json()["resultados"]]
        self.assertNotIn(pp_inactivo.pk, ids)

    def test_busqueda_devuelve_ultimo_costo_vigente(self):
        registrar_costo(self.pp, Decimal("135.75"), self.diego)
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self._buscar("VAL-100")
        resultado = next(
            fila for fila in response.json()["resultados"] if fila["id"] == self.pp.pk
        )
        self.assertEqual(resultado["costo_esperado"], "135.75")

    def test_busqueda_corta_no_devuelve_catalogo_completo(self):
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self._buscar("V")
        self.assertEqual(response.json()["resultados"], [])

    def test_busqueda_no_funciona_fuera_de_borrador(self):
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        _agregar_linea(self.orden, self.pp, costo="120.50")
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.EMITIDA,
            self.rodrigo,
        )
        response = self._buscar("VAL-100")
        self.assertEqual(response.status_code, 403)

    def test_producto_seleccionado_se_agrega_a_la_orden(self):
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self.client.post(
            reverse("purchasing:agregar_linea", args=[self.orden.pk]),
            {
                "producto_proveedor": self.pp.pk,
                "cantidad": "3",
                "costo_esperado": "125.00",
            },
        )
        self.assertRedirects(
            response,
            reverse("purchasing:detalle", args=[self.orden.pk]),
        )
        linea = LineaOrdenCompra.objects.get(orden=self.orden)
        self.assertEqual(linea.producto_proveedor, self.pp)
        self.assertEqual(linea.cantidad, Decimal("3"))
        self.assertEqual(linea.costo_esperado, Decimal("125.00"))

    def test_id_de_otro_proveedor_no_se_puede_forzar_por_post(self):
        pp_ajeno = _producto_proveedor(self.otro_proveedor, "FORZADO-01")
        self.client.login(username="rodrigo_buscador_oc", password="clave12345")
        response = self.client.post(
            reverse("purchasing:agregar_linea", args=[self.orden.pk]),
            {
                "producto_proveedor": pp_ajeno.pk,
                "cantidad": "1",
                "costo_esperado": "10.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            LineaOrdenCompra.objects.filter(
                orden=self.orden,
                producto_proveedor=pp_ajeno,
            ).exists()
        )
