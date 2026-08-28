from datetime import timedelta

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.utils import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto
from apps.clients.models import Cliente
from apps.quotes.models import Presupuesto
from apps.stock.models import Deposito

from .models import EstadoTarea, PrioridadTarea, Tarea, TipoAutomatizacion
from .permissions import puede_actualizar_estado, puede_gestionar_tareas, queryset_tareas_visibles
from .services import (
    TRANSICIONES_VALIDAS,
    TransicionInvalidaError,
    cambiar_estado_tarea,
    crear_tarea_automatica,
)


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class TareaModelTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_tarea_model", "Administrador")
        self.contri = _crear_usuario("contri_tarea_model", "Depósito")

    def test_str_es_el_titulo(self):
        tarea = Tarea.objects.create(titulo="Ordenar depósito", asignado_a=self.contri)
        self.assertEqual(str(tarea), "Ordenar depósito")

    def test_estado_por_defecto_pendiente(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        self.assertEqual(tarea.estado, EstadoTarea.PENDIENTE)

    def test_esta_vencida_sin_fecha_limite_es_falso(self):
        tarea = Tarea.objects.create(titulo="Sin fecha", asignado_a=self.contri)
        self.assertFalse(tarea.esta_vencida)

    def test_esta_vencida_con_fecha_pasada_y_no_completada(self):
        ayer = timezone.localdate() - timedelta(days=1)
        tarea = Tarea.objects.create(titulo="Vencida", asignado_a=self.contri, fecha_limite=ayer)
        self.assertTrue(tarea.esta_vencida)

    def test_no_esta_vencida_si_ya_fue_completada(self):
        ayer = timezone.localdate() - timedelta(days=1)
        tarea = Tarea.objects.create(
            titulo="Completada tarde", asignado_a=self.contri, fecha_limite=ayer,
            estado=EstadoTarea.COMPLETADA,
        )
        self.assertFalse(tarea.esta_vencida)

    def test_asignado_a_queda_null_si_se_borra_el_usuario(self):
        empleado = _crear_usuario("empleado_borrable", "Depósito")
        tarea = Tarea.objects.create(titulo="X", asignado_a=empleado)
        empleado.delete()
        tarea.refresh_from_db()
        self.assertIsNone(tarea.asignado_a)


class TransicionesTareaTests(TestCase):
    def setUp(self):
        self.contri = _crear_usuario("contri_transiciones", "Depósito")
        self.rodrigo = _crear_usuario("rodrigo_transiciones", "Ventas y Presupuestos")

    def test_pendiente_a_en_proceso(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        cambiar_estado_tarea(tarea, EstadoTarea.EN_PROCESO, self.contri)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, EstadoTarea.EN_PROCESO)
        self.assertIsNone(tarea.completada_en)

    def test_pendiente_a_completada_directo_esta_permitido(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        cambiar_estado_tarea(tarea, EstadoTarea.COMPLETADA, self.contri)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, EstadoTarea.COMPLETADA)
        self.assertIsNotNone(tarea.completada_en)

    def test_completada_es_terminal(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri, estado=EstadoTarea.COMPLETADA)
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_tarea(tarea, EstadoTarea.PENDIENTE, self.contri)

    def test_en_proceso_no_vuelve_a_pendiente(self):
        tarea = Tarea.objects.create(
            titulo="X", asignado_a=self.contri, estado=EstadoTarea.EN_PROCESO
        )
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_tarea(tarea, EstadoTarea.PENDIENTE, self.contri)

    def test_todas_las_aristas_declaradas_funcionan(self):
        for origen, destinos in TRANSICIONES_VALIDAS.items():
            for destino in destinos:
                with self.subTest(origen=origen, destino=destino):
                    tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri, estado=origen)
                    cambiar_estado_tarea(tarea, destino, self.rodrigo)
                    tarea.refresh_from_db()
                    self.assertEqual(tarea.estado, destino)


class PermisosTareaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_permisos_tarea", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_permisos_tarea", "Ventas y Presupuestos")
        cls.contri = _crear_usuario("contri_permisos_tarea", "Depósito")
        cls.andres = _crear_usuario("andres_permisos_tarea", "Técnico de Campo")

    def test_solo_diego_rodrigo_gabriel_pueden_gestionar(self):
        self.assertTrue(puede_gestionar_tareas(self.diego))
        self.assertTrue(puede_gestionar_tareas(self.rodrigo))
        self.assertFalse(puede_gestionar_tareas(self.contri))
        self.assertFalse(puede_gestionar_tareas(self.andres))

    def test_contri_puede_actualizar_estado_de_su_propia_tarea(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        self.assertTrue(puede_actualizar_estado(self.contri, tarea))

    def test_andres_no_puede_actualizar_estado_de_tarea_ajena(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        self.assertFalse(puede_actualizar_estado(self.andres, tarea))

    def test_rodrigo_puede_actualizar_estado_de_tarea_ajena_por_ser_gestor(self):
        tarea = Tarea.objects.create(titulo="X", asignado_a=self.contri)
        self.assertTrue(puede_actualizar_estado(self.rodrigo, tarea))

    def test_alcance_de_visibilidad_por_rol(self):
        propia_de_contri = Tarea.objects.create(titulo="Propia Contri", asignado_a=self.contri)
        asignada_por_rodrigo_a_andres = Tarea.objects.create(
            titulo="Rodrigo->Andrés", asignado_a=self.andres, asignado_por=self.rodrigo
        )
        ajena = Tarea.objects.create(titulo="Ajena", asignado_a=self.andres, asignado_por=self.diego)

        visibles_contri = queryset_tareas_visibles(self.contri, Tarea.objects.all())
        self.assertCountEqual(list(visibles_contri), [propia_de_contri])

        visibles_rodrigo = queryset_tareas_visibles(self.rodrigo, Tarea.objects.all())
        self.assertCountEqual(list(visibles_rodrigo), [asignada_por_rodrigo_a_andres])

        visibles_diego = queryset_tareas_visibles(self.diego, Tarea.objects.all())
        self.assertCountEqual(
            list(visibles_diego), [propia_de_contri, asignada_por_rodrigo_a_andres, ajena]
        )


class TareaViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_vistas_tarea", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_vistas_tarea", "Ventas y Presupuestos")
        cls.contri = _crear_usuario("contri_vistas_tarea", "Depósito")
        cls.andres = _crear_usuario("andres_vistas_tarea", "Técnico de Campo")

    def test_contri_no_puede_crear_tarea(self):
        self.client.login(username="contri_vistas_tarea", password="clave12345")
        response = self.client.get(reverse("tasks:nueva"))
        self.assertEqual(response.status_code, 403)

    def test_rodrigo_puede_crear_y_asignar_tarea(self):
        self.client.login(username="rodrigo_vistas_tarea", password="clave12345")
        response = self.client.post(
            reverse("tasks:nueva"),
            {
                "titulo": "Separar materiales", "descripcion": "", "asignado_a": self.contri.pk,
                "prioridad": PrioridadTarea.ALTA, "fecha_limite": "",
            },
        )
        self.assertRedirects(response, reverse("tasks:lista"))
        tarea = Tarea.objects.get(titulo="Separar materiales")
        self.assertEqual(tarea.asignado_a, self.contri)
        self.assertEqual(tarea.asignado_por, self.rodrigo)

    def test_contri_ve_solo_sus_tareas_en_mis_tareas(self):
        propia = Tarea.objects.create(titulo="Propia", asignado_a=self.contri)
        Tarea.objects.create(titulo="Ajena", asignado_a=self.andres)

        self.client.login(username="contri_vistas_tarea", password="clave12345")
        response = self.client.get(reverse("tasks:mis_tareas"))
        self.assertEqual(response.status_code, 200)
        titulos = [t.titulo for t in response.context["tareas"]]
        self.assertEqual(titulos, [propia.titulo])

    def test_contri_puede_completar_su_propia_tarea_via_vista(self):
        tarea = Tarea.objects.create(titulo="Propia", asignado_a=self.contri)
        self.client.login(username="contri_vistas_tarea", password="clave12345")
        response = self.client.post(
            reverse("tasks:cambiar_estado", args=[tarea.pk]), {"estado": "completada"}
        )
        self.assertEqual(response.status_code, 302)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, EstadoTarea.COMPLETADA)

    def test_andres_no_puede_cambiar_estado_de_tarea_ajena_via_vista(self):
        tarea = Tarea.objects.create(titulo="De Contri", asignado_a=self.contri)
        self.client.login(username="andres_vistas_tarea", password="clave12345")
        response = self.client.post(
            reverse("tasks:cambiar_estado", args=[tarea.pk]), {"estado": "completada"}
        )
        self.assertEqual(response.status_code, 403)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, EstadoTarea.PENDIENTE)


class DashboardMisTareasWidgetTests(TestCase):
    def test_dashboard_muestra_tareas_propias(self):
        contri = _crear_usuario("contri_dashboard", "Depósito")
        Tarea.objects.create(titulo="Mi tarea del dashboard", asignado_a=contri)

        self.client.login(username="contri_dashboard", password="clave12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Mi tarea del dashboard")


class TareaGeneradaPorConstraintTests(TestCase):
    """
    CheckConstraint tarea_generada_por_coherente_con_campos (Etapa 9):
    generada_por vacío exige presupuesto y producto en null; los dos
    tipos de automatización de Presupuesto exigen presupuesto seteado
    y producto en null; stock_minimo exige lo inverso.
    """

    def setUp(self):
        self.diego = _crear_usuario("diego_tarea_constraint", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Constraint Tarea")
        self.presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        marca = Marca.objects.create(nombre="Marca Constraint Tarea")
        self.producto = Producto.objects.create(marca=marca, codigo="CT-1", nombre="Producto CT")

    def test_manual_no_puede_tener_presupuesto(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Tarea.objects.create(titulo="X", asignado_a=self.diego, presupuesto=self.presupuesto)

    def test_manual_no_puede_tener_producto(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Tarea.objects.create(titulo="X", asignado_a=self.diego, producto=self.producto)

    def test_seguimiento_exige_presupuesto(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Tarea.objects.create(
                    titulo="X", asignado_a=self.diego,
                    generada_por=TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
                )

    def test_stock_minimo_con_presupuesto_en_vez_de_producto_falla(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Tarea.objects.create(
                    titulo="X", asignado_a=self.diego,
                    generada_por=TipoAutomatizacion.STOCK_MINIMO,
                    presupuesto=self.presupuesto,
                )

    def test_stock_minimo_con_presupuesto_y_producto_a_la_vez_falla(self):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Tarea.objects.create(
                    titulo="X", asignado_a=self.diego,
                    generada_por=TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
                    presupuesto=self.presupuesto,
                    producto=self.producto,
                )

    def test_combinaciones_validas_no_fallan(self):
        Tarea.objects.create(titulo="Manual", asignado_a=self.diego)
        Tarea.objects.create(
            titulo="Seguimiento", asignado_a=self.diego,
            generada_por=TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO, presupuesto=self.presupuesto,
        )
        Tarea.objects.create(
            titulo="Por vencer", asignado_a=self.diego,
            generada_por=TipoAutomatizacion.PRESUPUESTO_POR_VENCER, presupuesto=self.presupuesto,
        )
        Tarea.objects.create(
            titulo="Stock", asignado_a=self.diego,
            generada_por=TipoAutomatizacion.STOCK_MINIMO, producto=self.producto, deposito=Deposito.GENERAL,
        )
        self.assertEqual(Tarea.objects.count(), 4)


class CrearTareaAutomaticaTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_crear_tarea_auto", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Crear Tarea Auto")
        self.presupuesto = Presupuesto.objects.create(cliente=self.cliente)

    def test_crea_con_asignado_por_null_y_audita(self):
        from apps.audit.models import AuditLog

        tarea = crear_tarea_automatica(
            TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO,
            titulo="Seguimiento",
            descripcion="Detalle",
            asignado_a=self.diego,
            presupuesto=self.presupuesto,
        )
        self.assertIsNone(tarea.asignado_por)
        self.assertEqual(tarea.generada_por, TipoAutomatizacion.SEGUIMIENTO_PRESUPUESTO)
        self.assertEqual(tarea.estado, EstadoTarea.PENDIENTE)

        log = AuditLog.objects.filter(accion="generar_tarea_automatica").order_by("-creado_en").first()
        self.assertIsNone(log.usuario)
        self.assertIn("Seguimiento", log.detalle)
