from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto
from apps.clients.models import Cliente
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto
from apps.stock.models import Deposito, MovimientoStock, TipoMovimiento
from apps.stock.services import registrar_movimiento

from .models import EstadoTrabajo, MaterialTrabajo
from .permissions import puede_finalizar_trabajo
from .services import (
    TransicionInvalidaError,
    cambiar_estado_trabajo,
    cancelar_trabajo,
    crear_trabajo,
    enviar_materiales_pendientes,
    finalizar_trabajo,
    generar_listado_materiales,
    registrar_sobrante,
)


def _usuario(username, rol):
    grupo = Group.objects.get(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class CierreOperativoTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_10f", "Administrador")
        self.andres = _usuario("andres_10f", "Técnico de Campo")
        self.otro_tecnico = _usuario("otro_10f", "Técnico de Campo")
        self.contri = _usuario("contri_10f", "Depósito")

        marca = Marca.objects.create(nombre="Marca 10F")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="10F-MAT",
            nombre="Material 10F",
        )
        cliente = Cliente.objects.create(nombre="Cliente 10F")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            producto=self.producto,
            cantidad=Decimal("2"),
            precio_unitario=Decimal("100"),
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(
            presupuesto,
            EstadoPresupuesto.ACEPTADO,
            self.diego,
        )
        self.trabajo = crear_trabajo(
            presupuesto,
            self.diego,
            tecnico_asignado=self.andres,
        )
        generar_listado_materiales(self.trabajo, self.diego)
        self.material = self.trabajo.materiales.get()

        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"),
            usuario=self.diego,
        )

    def _en_ejecucion(self):
        cambiar_estado_trabajo(
            self.trabajo,
            EstadoTrabajo.EN_EJECUCION,
            self.diego,
        )

    def _listo_para_cerrar(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        self._en_ejecucion()

    def test_terminado_no_se_alcanza_por_cambio_generico(self):
        with self.assertRaisesMessage(
            TransicionInvalidaError,
            "cierre operativo",
        ):
            cambiar_estado_trabajo(
                self.trabajo,
                EstadoTrabajo.TERMINADO,
                self.diego,
            )

    def test_finalizar_exige_en_ejecucion(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)

        with self.assertRaisesMessage(
            TransicionInvalidaError,
            "En ejecución",
        ):
            finalizar_trabajo(self.trabajo, self.diego)

    def test_finalizar_exige_tecnico_asignado(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        self.trabajo.tecnico_asignado = None
        self.trabajo.save(update_fields=["tecnico_asignado"])
        self._en_ejecucion()

        with self.assertRaisesMessage(ValueError, "sin técnico"):
            finalizar_trabajo(self.trabajo, self.diego)

    def test_finalizar_bloquea_materiales_pendientes(self):
        self._en_ejecucion()

        with self.assertRaisesMessage(ValueError, "sin enviar"):
            finalizar_trabajo(self.trabajo, self.diego)

        self.trabajo.refresh_from_db()
        self.assertEqual(
            self.trabajo.estado,
            EstadoTrabajo.EN_EJECUCION,
        )

    def test_tecnico_asignado_puede_finalizar_y_guarda_metadata(self):
        self._listo_para_cerrar()

        cerrado = finalizar_trabajo(
            self.trabajo,
            self.andres,
            observaciones="Trabajo probado y entregado",
        )

        self.assertEqual(cerrado.estado, EstadoTrabajo.TERMINADO)
        self.assertEqual(cerrado.terminado_por, self.andres)
        self.assertIsNotNone(cerrado.terminado_en)
        self.assertEqual(
            cerrado.observaciones_cierre,
            "Trabajo probado y entregado",
        )

        log = AuditLog.objects.latest("id")
        self.assertEqual(log.accion, "finalizar_trabajo")
        self.assertIn("Trabajo finalizado", log.detalle)

    def test_otro_tecnico_no_puede_finalizar(self):
        self._listo_para_cerrar()

        self.assertFalse(
            puede_finalizar_trabajo(
                self.otro_tecnico,
                self.trabajo,
            )
        )
        with self.assertRaises(PermissionError):
            finalizar_trabajo(
                self.trabajo,
                self.otro_tecnico,
            )

    def test_finalizacion_es_atomica_si_falla_auditoria(self):
        self._listo_para_cerrar()

        with patch(
            "apps.jobs.services.log_action",
            side_effect=RuntimeError("audit caído"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit caído"):
                finalizar_trabajo(self.trabajo, self.diego)

        self.trabajo.refresh_from_db()
        self.assertEqual(
            self.trabajo.estado,
            EstadoTrabajo.EN_EJECUCION,
        )
        self.assertIsNone(self.trabajo.terminado_en)
        self.assertIsNone(self.trabajo.terminado_por)

    def test_terminado_no_se_puede_reabrir_por_transicion_generica(self):
        self._listo_para_cerrar()
        finalizar_trabajo(self.trabajo, self.diego)

        with self.assertRaisesMessage(
            TransicionInvalidaError,
            "cerrado",
        ):
            cambiar_estado_trabajo(
                self.trabajo,
                EstadoTrabajo.EN_EJECUCION,
                self.diego,
            )

    def test_no_se_puede_registrar_sobrante_despues_de_cerrar(self):
        self._listo_para_cerrar()
        finalizar_trabajo(self.trabajo, self.diego)

        with self.assertRaisesMessage(ValueError, "cerrado"):
            registrar_sobrante(
                self.material,
                Decimal("1"),
                self.diego,
            )

    def test_cancelar_exige_motivo_y_guarda_metadata(self):
        with self.assertRaisesMessage(ValueError, "motivo"):
            cancelar_trabajo(self.trabajo, self.diego)

        cancelar_trabajo(
            self.trabajo,
            self.diego,
            motivo="Cliente suspendió definitivamente la obra",
        )
        self.trabajo.refresh_from_db()

        self.assertEqual(
            self.trabajo.estado,
            EstadoTrabajo.CANCELADO,
        )
        self.assertEqual(self.trabajo.cancelado_por, self.diego)
        self.assertIsNotNone(self.trabajo.cancelado_en)
        self.assertEqual(
            self.trabajo.motivo_cancelacion,
            "Cliente suspendió definitivamente la obra",
        )


class CierreTrabajoPostgresTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_db_10f", "Administrador")
        self.andres = _usuario("andres_db_10f", "Técnico de Campo")
        marca = Marca.objects.create(nombre="Marca DB 10F")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="10F-DB",
            nombre="Material DB 10F",
        )
        cliente = Cliente.objects.create(nombre="Cliente DB 10F")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            producto=self.producto,
            cantidad=Decimal("2"),
            precio_unitario=Decimal("100"),
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(
            presupuesto,
            EstadoPresupuesto.ACEPTADO,
            self.diego,
        )
        self.trabajo = crear_trabajo(
            presupuesto,
            self.diego,
            tecnico_asignado=self.andres,
        )
        generar_listado_materiales(self.trabajo, self.diego)
        self.material = self.trabajo.materiales.get()

        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"),
            usuario=self.diego,
        )

    def test_db_rechaza_salto_directo_a_terminado(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                type(self.trabajo).objects.filter(
                    pk=self.trabajo.pk
                ).update(
                    estado=EstadoTrabajo.TERMINADO,
                    terminado_en=timezone.now(),
                    terminado_por=self.diego,
                )

    def test_db_rechaza_terminado_con_material_pendiente(self):
        cambiar_estado_trabajo(
            self.trabajo,
            EstadoTrabajo.EN_EJECUCION,
            self.diego,
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                type(self.trabajo).objects.filter(
                    pk=self.trabajo.pk
                ).update(
                    estado=EstadoTrabajo.TERMINADO,
                    terminado_en=timezone.now(),
                    terminado_por=self.diego,
                )

    def _cerrar_valido(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        cambiar_estado_trabajo(
            self.trabajo,
            EstadoTrabajo.EN_EJECUCION,
            self.diego,
        )
        finalizar_trabajo(self.trabajo, self.diego)

    def test_db_bloquea_editar_material_despues_del_cierre(self):
        self._cerrar_valido()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                MaterialTrabajo.objects.filter(
                    pk=self.material.pk
                ).update(cantidad_necesaria=Decimal("3"))

    def test_db_bloquea_nuevo_movimiento_stock_del_trabajo_cerrado(self):
        self._cerrar_valido()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto,
                    deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.ENTRADA,
                    cantidad=Decimal("1"),
                    registrado_por=self.diego,
                    trabajo=self.trabajo,
                )

    def test_db_rechaza_cancelacion_sin_metadata(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                type(self.trabajo).objects.filter(
                    pk=self.trabajo.pk
                ).update(
                    estado=EstadoTrabajo.CANCELADO,
                )


class CierreTrabajoVistasTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_view_10f", "Administrador")
        self.andres = _usuario("andres_view_10f", "Técnico de Campo")
        self.otro = _usuario("otro_view_10f", "Técnico de Campo")
        marca = Marca.objects.create(nombre="Marca View 10F")
        self.producto = Producto.objects.create(
            marca=marca,
            codigo="10F-V",
            nombre="Material View 10F",
        )
        cliente = Cliente.objects.create(nombre="Cliente View 10F")
        presupuesto = Presupuesto.objects.create(cliente=cliente)
        ItemPresupuesto.objects.create(
            presupuesto=presupuesto,
            producto=self.producto,
            cantidad=Decimal("1"),
            precio_unitario=Decimal("100"),
        )
        enviar_presupuesto(presupuesto, self.diego)
        cambiar_estado(
            presupuesto,
            EstadoPresupuesto.ACEPTADO,
            self.diego,
        )
        self.trabajo = crear_trabajo(
            presupuesto,
            self.diego,
            tecnico_asignado=self.andres,
        )
        generar_listado_materiales(self.trabajo, self.diego)
        registrar_movimiento(
            producto=self.producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("5"),
            usuario=self.diego,
        )
        cambiar_estado_trabajo(
            self.trabajo,
            EstadoTrabajo.EN_EJECUCION,
            self.diego,
        )

    def test_detalle_muestra_bloqueo_si_faltan_materiales(self):
        self.client.login(
            username=self.diego.username,
            password="clave12345",
        )
        response = self.client.get(
            reverse("jobs:detalle", args=[self.trabajo.pk])
        )

        self.assertContains(
            response,
            "todavía no se puede finalizar",
        )
        self.assertContains(response, "sin enviar")
        self.assertNotContains(response, ">Finalizar trabajo<")

    def test_detalle_ofrece_finalizar_cuando_esta_conciliado(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        self.client.login(
            username=self.andres.username,
            password="clave12345",
        )
        response = self.client.get(
            reverse("jobs:detalle", args=[self.trabajo.pk])
        )

        self.assertContains(response, "Finalizar trabajo")
        self.assertNotContains(response, "Marcar Terminado")

    def test_tecnico_ajeno_no_puede_usar_endpoint_finalizar(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        self.client.login(
            username=self.otro.username,
            password="clave12345",
        )
        response = self.client.post(
            reverse("jobs:finalizar", args=[self.trabajo.pk]),
            {"observaciones": ""},
        )
        self.assertEqual(response.status_code, 403)

    def test_finalizar_via_vista_congela_operacion(self):
        enviar_materiales_pendientes(self.trabajo, self.diego)
        self.client.login(
            username=self.andres.username,
            password="clave12345",
        )
        response = self.client.post(
            reverse("jobs:finalizar", args=[self.trabajo.pk]),
            {"observaciones": "Entrega conforme"},
        )

        self.assertRedirects(
            response,
            reverse("jobs:detalle", args=[self.trabajo.pk]),
        )
        self.trabajo.refresh_from_db()
        self.assertEqual(
            self.trabajo.estado,
            EstadoTrabajo.TERMINADO,
        )

        detalle = self.client.get(
            reverse("jobs:detalle", args=[self.trabajo.pk])
        )
        self.assertContains(detalle, "Este trabajo está cerrado")
        self.assertContains(detalle, "Entrega conforme")
