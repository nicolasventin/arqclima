from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Marca, Producto, Proveedor
from apps.clients.models import Cliente
from apps.jobs.models import EstadoTrabajo
from apps.jobs.services import crear_trabajo
from apps.quotes.models import EstadoPresupuesto, ItemPresupuesto, Presupuesto
from apps.quotes.services import cambiar_estado, enviar_presupuesto
from apps.stock.models import Deposito, TipoMovimiento
from apps.stock.services import registrar_movimiento
from apps.tasks.models import EstadoTarea, Tarea


def _usuario(username, rol="Administrador"):
    grupo, _ = Group.objects.get_or_create(name=rol)
    user = User.objects.create_user(username=username, password="clave12345")
    user.groups.add(grupo)
    return user


class ListadosEtapa11CTests(TestCase):
    def setUp(self):
        self.diego = _usuario("diego_11c")
        self.client.login(username="diego_11c", password="clave12345")

    def test_clientes_usa_shell_de_tabla_y_busqueda(self):
        Cliente.objects.create(nombre="Alfa Climatización", email="alfa@example.com")
        Cliente.objects.create(nombre="Beta Instalaciones", email="beta@example.com")

        response = self.client.get(reverse("clients:lista"), {"q": "Alfa"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-panel")
        self.assertContains(response, "filter-bar")
        self.assertContains(response, "Alfa Climatización")
        self.assertNotContains(response, "Beta Instalaciones")

    def test_productos_filtran_y_muestran_estado_visual(self):
        marca = Marca.objects.create(nombre="Marca 11C")
        general = Producto.objects.create(
            marca=marca,
            codigo="GEN-11C",
            nombre="Equipo general",
            es_repuesto=False,
        )
        Producto.objects.create(
            marca=marca,
            codigo="REP-11C",
            nombre="Repuesto específico",
            es_repuesto=True,
        )

        response = self.client.get(
            reverse("catalog:producto_lista"),
            {"linea": "general", "q": "Equipo"},
        )

        self.assertContains(response, general.nombre)
        self.assertNotContains(response, "Repuesto específico")
        self.assertContains(response, "status-pill")

    def test_proveedores_tienen_busqueda_real(self):
        Proveedor.objects.create(nombre_comercial="Proveedor Norte", email="norte@example.com")
        Proveedor.objects.create(nombre_comercial="Proveedor Sur", email="sur@example.com")

        response = self.client.get(reverse("catalog:proveedor_lista"), {"q": "Norte"})

        self.assertContains(response, "Proveedor Norte")
        self.assertNotContains(response, "Proveedor Sur")

    def test_trabajos_filtran_despues_del_scope_visible(self):
        cliente_a = Cliente.objects.create(nombre="Cliente Obra A")
        cliente_b = Cliente.objects.create(nombre="Cliente Obra B")

        def crear(cliente, direccion):
            presupuesto = Presupuesto.objects.create(cliente=cliente, direccion=direccion)
            ItemPresupuesto.objects.create(
                presupuesto=presupuesto,
                descripcion_manual="Instalación",
                precio_unitario=Decimal("100"),
            )
            enviar_presupuesto(presupuesto, self.diego)
            cambiar_estado(presupuesto, EstadoPresupuesto.ACEPTADO, self.diego)
            return crear_trabajo(presupuesto, self.diego)

        trabajo_a = crear(cliente_a, "San Martín 100")
        crear(cliente_b, "Belgrano 200")

        response = self.client.get(reverse("jobs:lista"), {"q": "Obra A"})

        self.assertContains(response, f"#{trabajo_a.pk}")
        self.assertContains(response, "Cliente Obra A")
        self.assertNotContains(response, "Cliente Obra B")
        self.assertContains(response, "Estado")

    def test_tareas_buscan_por_titulo_y_estado(self):
        Tarea.objects.create(
            titulo="Comprar válvulas",
            asignado_a=self.diego,
            asignado_por=self.diego,
            estado=EstadoTarea.PENDIENTE,
        )
        Tarea.objects.create(
            titulo="Revisar factura",
            asignado_a=self.diego,
            asignado_por=self.diego,
            estado=EstadoTarea.EN_PROCESO,
        )

        response = self.client.get(reverse("tasks:lista"), {"q": "válvulas"})

        self.assertContains(response, "Comprar válvulas")
        self.assertNotContains(response, "Revisar factura")
        self.assertContains(response, "status-pill")

    def test_movimientos_buscan_y_conservan_semantica_de_signo(self):
        marca = Marca.objects.create(nombre="Marca Stock 11C")
        producto = Producto.objects.create(
            marca=marca,
            codigo="STK-11C",
            nombre="Válvula de prueba",
        )
        registrar_movimiento(
            producto=producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("10"),
            usuario=self.diego,
            referencia_libre="Carga inicial 11C",
        )
        registrar_movimiento(
            producto=producto,
            deposito=Deposito.GENERAL,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("-2"),
            usuario=self.diego,
            referencia_libre="Obra Centro",
        )

        response = self.client.get(
            reverse("stock:movimientos"),
            {"q": "Obra Centro", "deposito": "general"},
        )

        self.assertContains(response, "Obra Centro")
        self.assertNotContains(response, "Carga inicial 11C")
        self.assertContains(response, "movement-negative")

    def test_paginacion_comun_esta_presente_cuando_hay_mas_de_50(self):
        Cliente.objects.bulk_create(
            [Cliente(nombre=f"Cliente {i:02d}") for i in range(51)]
        )

        response = self.client.get(reverse("clients:lista"), {"q": "Cliente"})

        self.assertTrue(response.context["is_paginated"])
        self.assertContains(response, "Página 1 de 2")
        self.assertContains(response, "Siguiente")
