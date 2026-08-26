from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.clients.models import Cliente
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto

from .models import EstadoTrabajo, ORDEN_ESTADOS, Trabajo
from .permissions import (
    puede_asignar_tecnico,
    puede_cambiar_estado_trabajo,
    puede_cancelar_trabajo,
    puede_crear_trabajo,
    queryset_trabajos_visibles,
)
from .services import TransicionInvalidaError, cambiar_estado_trabajo, cancelar_trabajo, crear_trabajo


def _crear_usuario(username, rol):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


def _presupuesto_aceptado(cliente, usuario):
    presupuesto = Presupuesto.objects.create(cliente=cliente, direccion="Calle Falsa 123")
    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, descripcion_manual="Item", precio_unitario=Decimal("100")
    )
    enviar_presupuesto(presupuesto, usuario)
    cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, usuario)
    return presupuesto


class CrearTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_crear_trabajo", "Administrador")
        self.cliente = Cliente.objects.create(nombre="Cliente Crear Trabajo")

    def test_hereda_direccion_y_notas_del_presupuesto(self):
        presupuesto = _presupuesto_aceptado(self.cliente, self.diego)
        presupuesto.notas_generales = "Cuidado con el perro"
        presupuesto.save()

        trabajo = crear_trabajo(presupuesto, self.diego)

        self.assertEqual(trabajo.direccion, presupuesto.direccion)
        self.assertEqual(trabajo.observaciones, "Cuidado con el perro")
        self.assertEqual(trabajo.estado, EstadoTrabajo.PENDIENTE)

    def test_direccion_del_trabajo_es_independiente_despues(self):
        presupuesto = _presupuesto_aceptado(self.cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        trabajo.direccion = "Otra dirección — depósito auxiliar"
        trabajo.save()

        presupuesto.refresh_from_db()
        self.assertNotEqual(trabajo.direccion, presupuesto.direccion)

    def test_no_se_puede_crear_desde_presupuesto_no_aceptado(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        with self.assertRaises(ValueError):
            crear_trabajo(presupuesto, self.diego)

    def test_no_se_puede_crear_dos_veces_para_el_mismo_presupuesto(self):
        presupuesto = _presupuesto_aceptado(self.cliente, self.diego)
        crear_trabajo(presupuesto, self.diego)
        with self.assertRaises(ValueError):
            crear_trabajo(presupuesto, self.diego)

    def test_presupuesto_trabajo_es_accesible_via_relacion_inversa(self):
        presupuesto = _presupuesto_aceptado(self.cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)
        self.assertEqual(presupuesto.trabajo, trabajo)


class TransicionesTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_transiciones_trabajo", "Administrador")
        cliente = Cliente.objects.create(nombre="Cliente Transiciones Trabajo")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        self.trabajo = crear_trabajo(presupuesto, self.diego)

    def test_avanza_un_paso(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES, self.diego)
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.PREPARANDO_MATERIALES)

    def test_permite_saltear_etapas(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.LISTO, self.diego)
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.LISTO)

    def test_permite_saltar_hasta_terminado(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.TERMINADO, self.diego)
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.TERMINADO)

    def test_permite_retroceder_para_corregir_un_error(self):
        """
        El servicio en sí no bloquea retroceder — Presupuesto tampoco
        es estrictamente irreversible (Enviado→Borrador, Rechazado→
        Borrador), y acá con más razón: Trabajo es OneToOne con su
        Presupuesto, así que ni existe el workaround de "crear uno
        nuevo" que tiene Tarea si un estado se avanza por error.
        """
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.LISTO, self.diego)
        cambiar_estado_trabajo(
            self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES, self.diego,
            detalle="Se marcó Listo por error, faltaba material",
        )
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.PREPARANDO_MATERIALES)

    def test_no_puede_quedarse_en_el_mismo_estado(self):
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.PENDIENTE, self.diego)

    def test_orden_estados_completo(self):
        self.assertEqual(
            ORDEN_ESTADOS,
            [
                EstadoTrabajo.PENDIENTE,
                EstadoTrabajo.PREPARANDO_MATERIALES,
                EstadoTrabajo.LISTO,
                EstadoTrabajo.EN_EJECUCION,
                EstadoTrabajo.TERMINADO,
            ],
        )


class PermisosTrabajoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_permisos_trabajo", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_permisos_trabajo", "Ventas y Presupuestos")
        cls.contri = _crear_usuario("contri_permisos_trabajo", "Depósito")
        cls.andres = _crear_usuario("andres_permisos_trabajo", "Técnico de Campo")
        cls.otro_tecnico = _crear_usuario("otro_tecnico_permisos_trabajo", "Técnico de Campo")

    def setUp(self):
        cliente = Cliente.objects.create(nombre="Cliente Permisos Trabajo")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        self.trabajo = crear_trabajo(presupuesto, self.diego, tecnico_asignado=self.andres)

    def test_puede_crear_trabajo_solo_diego_y_rodrigo(self):
        self.assertTrue(puede_crear_trabajo(self.diego))
        self.assertTrue(puede_crear_trabajo(self.rodrigo))
        self.assertFalse(puede_crear_trabajo(self.contri))
        self.assertFalse(puede_crear_trabajo(self.andres))

    def test_diego_puede_cualquier_transicion(self):
        self.assertTrue(
            puede_cambiar_estado_trabajo(self.diego, self.trabajo, EstadoTrabajo.TERMINADO)
        )

    def test_rodrigo_no_puede_cambiar_estado(self):
        self.assertFalse(
            puede_cambiar_estado_trabajo(self.rodrigo, self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES)
        )

    def test_contri_puede_preparacion_no_ejecucion(self):
        self.assertTrue(
            puede_cambiar_estado_trabajo(self.contri, self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES)
        )
        self.assertTrue(puede_cambiar_estado_trabajo(self.contri, self.trabajo, EstadoTrabajo.LISTO))
        self.assertFalse(
            puede_cambiar_estado_trabajo(self.contri, self.trabajo, EstadoTrabajo.EN_EJECUCION)
        )

    def test_andres_puede_ejecucion_de_su_propio_trabajo(self):
        self.assertTrue(
            puede_cambiar_estado_trabajo(self.andres, self.trabajo, EstadoTrabajo.EN_EJECUCION)
        )
        self.assertTrue(puede_cambiar_estado_trabajo(self.andres, self.trabajo, EstadoTrabajo.TERMINADO))

    def test_andres_no_puede_ejecucion_de_trabajo_ajeno(self):
        self.assertFalse(
            puede_cambiar_estado_trabajo(self.otro_tecnico, self.trabajo, EstadoTrabajo.EN_EJECUCION)
        )

    def test_andres_no_puede_preparacion(self):
        self.assertFalse(
            puede_cambiar_estado_trabajo(self.andres, self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES)
        )

    def test_contri_puede_retroceder_un_listo_marcado_por_error(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.LISTO, self.diego)
        self.assertTrue(
            puede_cambiar_estado_trabajo(self.contri, self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES)
        )

    def test_andres_puede_retroceder_dentro_de_su_propio_rango(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.TERMINADO, self.diego)
        self.assertTrue(
            puede_cambiar_estado_trabajo(self.andres, self.trabajo, EstadoTrabajo.EN_EJECUCION)
        )

    def test_andres_no_puede_retroceder_al_territorio_de_contri(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.EN_EJECUCION, self.diego)
        self.assertFalse(puede_cambiar_estado_trabajo(self.andres, self.trabajo, EstadoTrabajo.LISTO))

    def test_solo_diego_puede_retroceder_hasta_pendiente(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.PREPARANDO_MATERIALES, self.diego)
        self.assertFalse(
            puede_cambiar_estado_trabajo(self.contri, self.trabajo, EstadoTrabajo.PENDIENTE)
        )
        self.assertTrue(puede_cambiar_estado_trabajo(self.diego, self.trabajo, EstadoTrabajo.PENDIENTE))

    def test_visibilidad_andres_solo_lo_propio(self):
        otro_trabajo = crear_trabajo(
            _presupuesto_aceptado(Cliente.objects.create(nombre="Otro cliente"), self.diego),
            self.diego,
            tecnico_asignado=self.otro_tecnico,
        )
        visibles = queryset_trabajos_visibles(self.andres, Trabajo.objects.all())
        self.assertCountEqual(list(visibles), [self.trabajo])
        self.assertNotIn(otro_trabajo, visibles)

    def test_visibilidad_contri_ve_todo(self):
        visibles = queryset_trabajos_visibles(self.contri, Trabajo.objects.all())
        self.assertIn(self.trabajo, visibles)


class TrabajoViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_vistas_trabajo", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_vistas_trabajo", "Ventas y Presupuestos")
        cls.contri = _crear_usuario("contri_vistas_trabajo", "Depósito")
        cls.andres = _crear_usuario("andres_vistas_trabajo", "Técnico de Campo")

    def test_rodrigo_puede_crear_trabajo_pero_no_asignar_tecnico(self):
        """
        Asignar técnico es exclusivo de Diego (coordinación de obra) —
        aunque Rodrigo pueda crear el trabajo, un intento de mandar
        tecnico_asignado en el POST se ignora silenciosamente.
        """
        cliente = Cliente.objects.create(nombre="Cliente Vista Crear")
        presupuesto = _presupuesto_aceptado(cliente, self.rodrigo)

        self.client.login(username="rodrigo_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:crear", args=[presupuesto.pk]), {"tecnico_asignado": self.andres.pk}
        )
        trabajo = Trabajo.objects.get(presupuesto=presupuesto)
        self.assertRedirects(response, reverse("jobs:detalle", args=[trabajo.pk]))
        self.assertIsNone(trabajo.tecnico_asignado)

    def test_diego_puede_asignar_tecnico_al_crear(self):
        cliente = Cliente.objects.create(nombre="Cliente Vista Crear Diego")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)

        self.client.login(username="diego_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:crear", args=[presupuesto.pk]), {"tecnico_asignado": self.andres.pk}
        )
        trabajo = Trabajo.objects.get(presupuesto=presupuesto)
        self.assertRedirects(response, reverse("jobs:detalle", args=[trabajo.pk]))
        self.assertEqual(trabajo.tecnico_asignado, self.andres)

    def test_diego_puede_reasignar_tecnico_despues_de_creado(self):
        cliente = Cliente.objects.create(nombre="Cliente Reasignar")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        self.client.login(username="diego_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:asignar_tecnico", args=[trabajo.pk]), {"tecnico_asignado": self.andres.pk}
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[trabajo.pk]))
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.tecnico_asignado, self.andres)

    def test_rodrigo_no_puede_reasignar_tecnico(self):
        cliente = Cliente.objects.create(nombre="Cliente Reasignar Sin Permiso")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        self.client.login(username="rodrigo_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:asignar_tecnico", args=[trabajo.pk]), {"tecnico_asignado": self.andres.pk}
        )
        self.assertEqual(response.status_code, 403)
        trabajo.refresh_from_db()
        self.assertIsNone(trabajo.tecnico_asignado)

    def test_no_se_puede_crear_trabajo_dos_veces_via_vista(self):
        cliente = Cliente.objects.create(nombre="Cliente Vista Duplicado")
        presupuesto = _presupuesto_aceptado(cliente, self.rodrigo)
        crear_trabajo(presupuesto, self.diego)

        self.client.login(username="rodrigo_vistas_trabajo", password="clave12345")
        response = self.client.post(reverse("jobs:crear", args=[presupuesto.pk]), {})
        self.assertRedirects(response, reverse("quotes:detalle", args=[presupuesto.pk]))
        self.assertEqual(Trabajo.objects.filter(presupuesto=presupuesto).count(), 1)

    def test_contri_no_puede_crear_trabajo(self):
        cliente = Cliente.objects.create(nombre="Cliente Vista Sin Permiso")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)

        self.client.login(username="contri_vistas_trabajo", password="clave12345")
        response = self.client.post(reverse("jobs:crear", args=[presupuesto.pk]), {})
        self.assertEqual(response.status_code, 403)

    def test_contri_puede_cambiar_estado_via_vista(self):
        cliente = Cliente.objects.create(nombre="Cliente Vista Estado")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)

        self.client.login(username="contri_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:cambiar_estado", args=[trabajo.pk]), {"estado": "listo"}
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[trabajo.pk]))
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, EstadoTrabajo.LISTO)

    def test_andres_no_puede_cambiar_estado_de_trabajo_ajeno_via_vista(self):
        otro_tecnico = _crear_usuario("otro_tecnico_vistas_trabajo", "Técnico de Campo")
        cliente = Cliente.objects.create(nombre="Cliente Vista Ajeno")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego, tecnico_asignado=otro_tecnico)

        self.client.login(username="andres_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:cambiar_estado", args=[trabajo.pk]), {"estado": "en_ejecucion"}
        )
        self.assertEqual(response.status_code, 403)

    def test_contri_puede_retroceder_via_vista(self):
        cliente = Cliente.objects.create(nombre="Cliente Vista Retroceder")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        trabajo = crear_trabajo(presupuesto, self.diego)
        cambiar_estado_trabajo(trabajo, EstadoTrabajo.LISTO, self.diego)

        self.client.login(username="contri_vistas_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:cambiar_estado", args=[trabajo.pk]),
            {"estado": "preparando_materiales"},
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[trabajo.pk]))
        trabajo.refresh_from_db()
        self.assertEqual(trabajo.estado, EstadoTrabajo.PREPARANDO_MATERIALES)


class CancelarTrabajoTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_cancelar_trabajo", "Administrador")
        self.rodrigo = _crear_usuario("rodrigo_cancelar_trabajo", "Ventas y Presupuestos")
        cliente = Cliente.objects.create(nombre="Cliente Cancelar Trabajo")
        presupuesto = _presupuesto_aceptado(cliente, self.diego)
        self.trabajo = crear_trabajo(presupuesto, self.diego)

    def test_cancela_desde_pendiente(self):
        cancelar_trabajo(self.trabajo, self.diego, motivo="El cliente se bajó")
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.CANCELADO)

    def test_cancela_desde_en_ejecucion(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.EN_EJECUCION, self.diego)
        cancelar_trabajo(self.trabajo, self.diego)
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.CANCELADO)

    def test_no_se_puede_cancelar_un_terminado(self):
        cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.TERMINADO, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cancelar_trabajo(self.trabajo, self.diego)

    def test_no_se_puede_cancelar_dos_veces(self):
        cancelar_trabajo(self.trabajo, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cancelar_trabajo(self.trabajo, self.diego)

    def test_un_cancelado_no_participa_de_avanzar_ni_retroceder(self):
        cancelar_trabajo(self.trabajo, self.diego)
        with self.assertRaises(TransicionInvalidaError):
            cambiar_estado_trabajo(self.trabajo, EstadoTrabajo.PENDIENTE, self.diego)

    def test_solo_diego_puede_cancelar(self):
        self.assertTrue(puede_cancelar_trabajo(self.diego))
        self.assertFalse(puede_cancelar_trabajo(self.rodrigo))

    def test_cancelar_via_vista(self):
        self.client.login(username="diego_cancelar_trabajo", password="clave12345")
        response = self.client.post(
            reverse("jobs:cancelar", args=[self.trabajo.pk]), {"motivo": "Obra abandonada"}
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.trabajo.refresh_from_db()
        self.assertEqual(self.trabajo.estado, EstadoTrabajo.CANCELADO)

    def test_rodrigo_no_puede_cancelar_via_vista(self):
        self.client.login(username="rodrigo_cancelar_trabajo", password="clave12345")
        response = self.client.post(reverse("jobs:cancelar", args=[self.trabajo.pk]), {})
        self.assertEqual(response.status_code, 403)
        self.trabajo.refresh_from_db()
        self.assertNotEqual(self.trabajo.estado, EstadoTrabajo.CANCELADO)


class PuedeAsignarTecnicoTests(TestCase):
    def test_solo_diego(self):
        diego = _crear_usuario("diego_puede_asignar", "Administrador")
        rodrigo = _crear_usuario("rodrigo_puede_asignar", "Ventas y Presupuestos")
        contri = _crear_usuario("contri_puede_asignar", "Depósito")
        self.assertTrue(puede_asignar_tecnico(diego))
        self.assertFalse(puede_asignar_tecnico(rodrigo))
        self.assertFalse(puede_asignar_tecnico(contri))
