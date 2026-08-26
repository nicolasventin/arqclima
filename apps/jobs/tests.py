from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto
from apps.clients.models import Cliente
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto, SeccionPresupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto

from .models import EstadoTrabajo, EtapaTrabajo, MaterialTrabajo, ORDEN_ESTADOS, Trabajo
from .permissions import (
    puede_asignar_tecnico,
    puede_cambiar_estado_trabajo,
    puede_cancelar_trabajo,
    puede_crear_trabajo,
    puede_gestionar_materiales,
    queryset_trabajos_visibles,
)
from .services import (
    TransicionInvalidaError,
    cambiar_estado_trabajo,
    cancelar_trabajo,
    crear_trabajo,
    generar_listado_materiales,
)


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


def _presupuesto_con_secciones_y_productos(cliente, usuario):
    """
    Presupuesto con 2 secciones y una mezcla de ítems de catálogo y
    manuales — para probar que generar_listado_materiales() agrupa
    bien por sección y descarta los conceptos manuales (mano de obra).
    """
    marca = Marca.objects.create(nombre=f"Marca Jobs {cliente.pk}")
    producto_a = Producto.objects.create(marca=marca, codigo="MAT-A", nombre="Caldera")
    producto_b = Producto.objects.create(marca=marca, codigo="MAT-B", nombre="Termostato")

    presupuesto = Presupuesto.objects.create(cliente=cliente, direccion="Obra con secciones")
    seccion_1 = SeccionPresupuesto.objects.create(presupuesto=presupuesto, titulo="1era etapa", orden=0)
    seccion_2 = SeccionPresupuesto.objects.create(presupuesto=presupuesto, titulo="2da etapa", orden=1)

    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, seccion=seccion_1, producto=producto_a,
        cantidad=Decimal("2"), precio_unitario=Decimal("1000"), orden=0,
    )
    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, seccion=seccion_2, producto=producto_b,
        cantidad=Decimal("1"), precio_unitario=Decimal("500"), orden=0,
    )
    # sin sección
    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, producto=producto_a,
        cantidad=Decimal("1"), precio_unitario=Decimal("1000"), orden=1,
    )
    # manual: no debe generar MaterialTrabajo
    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, descripcion_manual="Mano de obra",
        cantidad=Decimal("1"), precio_unitario=Decimal("5000"), orden=2,
    )
    # opcional no incluido: tampoco debe generar MaterialTrabajo
    ItemPresupuesto.objects.create(
        presupuesto=presupuesto, producto=producto_b,
        cantidad=Decimal("3"), precio_unitario=Decimal("500"),
        opcional=True, incluido=False, orden=3,
    )

    enviar_presupuesto(presupuesto, usuario)
    cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, usuario)
    return presupuesto


class GenerarListadoMaterialesTests(TestCase):
    def setUp(self):
        self.diego = _crear_usuario("diego_generar_listado", "Administrador")
        cliente = Cliente.objects.create(nombre="Cliente Generar Listado")
        self.presupuesto = _presupuesto_con_secciones_y_productos(cliente, self.diego)
        self.trabajo = crear_trabajo(self.presupuesto, self.diego)

    def test_crea_una_etapa_por_seccion_en_orden(self):
        generar_listado_materiales(self.trabajo, self.diego)
        titulos = list(self.trabajo.etapas.order_by("orden").values_list("titulo", flat=True))
        self.assertEqual(titulos, ["1era etapa", "2da etapa"])

    def test_asocia_materiales_a_su_etapa_correspondiente(self):
        generar_listado_materiales(self.trabajo, self.diego)
        etapa_1 = self.trabajo.etapas.get(titulo="1era etapa")
        material = etapa_1.materiales.get()
        self.assertEqual(material.producto.codigo, "MAT-A")
        self.assertEqual(material.cantidad_necesaria, Decimal("2"))

    def test_material_sin_seccion_queda_sin_etapa(self):
        generar_listado_materiales(self.trabajo, self.diego)
        sin_etapa = self.trabajo.materiales.filter(etapa__isnull=True)
        self.assertEqual(sin_etapa.count(), 1)
        self.assertEqual(sin_etapa.get().producto.codigo, "MAT-A")

    def test_excluye_items_manuales_y_opcionales_no_incluidos(self):
        generar_listado_materiales(self.trabajo, self.diego)
        # 3 ítems de catálogo incluidos en total (2 con sección + 1 sin sección);
        # el manual y el opcional-no-incluido quedan afuera.
        self.assertEqual(self.trabajo.materiales.count(), 3)
        codigos = set(self.trabajo.materiales.values_list("producto__codigo", flat=True))
        self.assertEqual(codigos, {"MAT-A", "MAT-B"})

    def test_no_se_puede_generar_dos_veces(self):
        generar_listado_materiales(self.trabajo, self.diego)
        with self.assertRaises(ValueError):
            generar_listado_materiales(self.trabajo, self.diego)


class MaterialTrabajoViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.diego = _crear_usuario("diego_vistas_material", "Administrador")
        cls.rodrigo = _crear_usuario("rodrigo_vistas_material", "Ventas y Presupuestos")
        cls.contri = _crear_usuario("contri_vistas_material", "Depósito")
        cls.marca = Marca.objects.create(nombre="Marca Vistas Material")
        cls.producto = Producto.objects.create(marca=cls.marca, codigo="VM-1", nombre="Caño")

    def setUp(self):
        cliente = Cliente.objects.create(nombre="Cliente Vistas Material")
        self.presupuesto = _presupuesto_con_secciones_y_productos(cliente, self.diego)
        self.trabajo = crear_trabajo(self.presupuesto, self.diego)

    def test_contri_puede_generar_el_listado_via_vista(self):
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(reverse("jobs:generar_materiales", args=[self.trabajo.pk]))
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertTrue(self.trabajo.materiales.exists())

    def test_rodrigo_no_puede_generar_el_listado(self):
        self.client.login(username="rodrigo_vistas_material", password="clave12345")
        response = self.client.post(reverse("jobs:generar_materiales", args=[self.trabajo.pk]))
        self.assertEqual(response.status_code, 403)

    def test_contri_puede_agregar_material_de_catalogo(self):
        generar_listado_materiales(self.trabajo, self.diego)
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(
            reverse("jobs:agregar_material_catalogo", args=[self.trabajo.pk]),
            {"etapa": "", "producto": self.producto.pk, "cantidad_necesaria": "4"},
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertTrue(self.trabajo.materiales.filter(producto=self.producto).exists())

    def test_contri_puede_agregar_material_manual(self):
        generar_listado_materiales(self.trabajo, self.diego)
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(
            reverse("jobs:agregar_material_manual", args=[self.trabajo.pk]),
            {"etapa": "", "descripcion_manual": "Caño extra sin catálogo", "cantidad_necesaria": "1"},
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertTrue(
            self.trabajo.materiales.filter(descripcion_manual="Caño extra sin catálogo").exists()
        )

    def test_actualizar_cantidad_material(self):
        generar_listado_materiales(self.trabajo, self.diego)
        material = self.trabajo.materiales.first()
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(
            reverse("jobs:actualizar_cantidad_material", args=[material.pk]),
            {"cantidad_necesaria": "99"},
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        material.refresh_from_db()
        self.assertEqual(material.cantidad_necesaria, Decimal("99.00"))

    def test_eliminar_material(self):
        generar_listado_materiales(self.trabajo, self.diego)
        material = self.trabajo.materiales.first()
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(reverse("jobs:eliminar_material", args=[material.pk]))
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertFalse(MaterialTrabajo.objects.filter(pk=material.pk).exists())

    def test_no_se_puede_eliminar_etapa_con_materiales(self):
        generar_listado_materiales(self.trabajo, self.diego)
        etapa = self.trabajo.etapas.get(titulo="1era etapa")
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(reverse("jobs:eliminar_etapa", args=[etapa.pk]))
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertTrue(EtapaTrabajo.objects.filter(pk=etapa.pk).exists())

    def test_eliminar_etapa_vacia(self):
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(
            reverse("jobs:agregar_etapa", args=[self.trabajo.pk]),
            {"titulo": "Etapa extra", "fecha_estimada": "", "duracion_estimada_dias": ""},
        )
        etapa = self.trabajo.etapas.get(titulo="Etapa extra")
        response = self.client.post(reverse("jobs:eliminar_etapa", args=[etapa.pk]))
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        self.assertFalse(EtapaTrabajo.objects.filter(pk=etapa.pk).exists())

    def test_agregar_etapa_con_fecha_y_duracion(self):
        self.client.login(username="contri_vistas_material", password="clave12345")
        response = self.client.post(
            reverse("jobs:agregar_etapa", args=[self.trabajo.pk]),
            {"titulo": "Etapa rápida", "fecha_estimada": "2026-09-01", "duracion_estimada_dias": "1"},
        )
        self.assertRedirects(response, reverse("jobs:detalle", args=[self.trabajo.pk]))
        etapa = self.trabajo.etapas.get(titulo="Etapa rápida")
        self.assertEqual(etapa.duracion_estimada_dias, 1)


class PuedeGestionarMaterialesTests(TestCase):
    def test_diego_y_contri_si_rodrigo_y_andres_no(self):
        diego = _crear_usuario("diego_gestionar_materiales", "Administrador")
        contri = _crear_usuario("contri_gestionar_materiales", "Depósito")
        rodrigo = _crear_usuario("rodrigo_gestionar_materiales", "Ventas y Presupuestos")
        andres = _crear_usuario("andres_gestionar_materiales", "Técnico de Campo")
        self.assertTrue(puede_gestionar_materiales(diego))
        self.assertTrue(puede_gestionar_materiales(contri))
        self.assertFalse(puede_gestionar_materiales(rodrigo))
        self.assertFalse(puede_gestionar_materiales(andres))
