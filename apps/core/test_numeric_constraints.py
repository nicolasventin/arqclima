from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Categoria, Marca, Producto, ProductoProveedor, Proveedor
from apps.clients.models import Cliente
from apps.jobs.forms import MaterialManualForm
from apps.jobs.models import EtapaTrabajo, MaterialTrabajo, Trabajo
from apps.pricing.forms import MargenMarcaForm
from apps.pricing.models import ConfiguracionGeneral, HistorialCosto
from apps.purchasing.forms import LineaOrdenCompraForm
from apps.purchasing.models import LineaOrdenCompra, OrdenDeCompra
from apps.quotes.forms import ItemManualForm, PresupuestoForm
from apps.quotes.models import ItemPresupuesto, Presupuesto, TipoDescuento
from apps.stock.forms import StockMinimoForm
from apps.stock.models import Deposito, MovimientoStock, TipoMovimiento
from apps.stock.services import registrar_movimiento


class _DatosBaseMixin:
    @classmethod
    def setUpTestData(cls):
        cls.marca = Marca.objects.create(nombre="Marca validaciones")
        cls.categoria = Categoria.objects.create(nombre="Categoría validaciones")
        cls.producto = Producto.objects.create(
            marca=cls.marca,
            codigo="VAL-1",
            nombre="Producto validaciones",
            categoria=cls.categoria,
        )
        cls.proveedor = Proveedor.objects.create(nombre_comercial="Proveedor validaciones")
        cls.producto_proveedor = ProductoProveedor.objects.create(
            producto=cls.producto,
            proveedor=cls.proveedor,
        )
        cls.cliente = Cliente.objects.create(nombre="Cliente validaciones")


class ConstraintsCatalogoPricingTests(_DatosBaseMixin, TestCase):
    def test_margen_negativo_no_se_puede_guardar(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Marca.objects.filter(pk=self.marca.pk).update(margen=Decimal("-0.01"))

    def test_stock_minimo_negativo_no_se_puede_guardar(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Producto.objects.filter(pk=self.producto.pk).update(
                    stock_minimo_general=Decimal("-1")
                )

    def test_costo_negativo_no_se_puede_insertar(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HistorialCosto.objects.create(
                    producto_proveedor=self.producto_proveedor,
                    costo=Decimal("-1"),
                )

    def test_configuracion_no_admite_porcentajes_negativos(self):
        config = ConfiguracionGeneral.obtener()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConfiguracionGeneral.objects.filter(pk=config.pk).update(
                    margen_general=Decimal("-1")
                )

    def test_iva_debe_estar_entre_0_y_100(self):
        config = ConfiguracionGeneral.obtener()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConfiguracionGeneral.objects.filter(pk=config.pk).update(
                    iva_pct=Decimal("100.01")
                )

    def test_dias_de_automatizacion_deben_ser_mayores_a_cero(self):
        config = ConfiguracionGeneral.obtener()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConfiguracionGeneral.objects.filter(pk=config.pk).update(
                    dias_seguimiento_presupuesto_enviado=0
                )


class ConstraintsPresupuestoTests(_DatosBaseMixin, TestCase):
    def test_cantidad_unidades_debe_ser_mayor_a_cero(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Presupuesto.objects.create(
                    cliente=self.cliente,
                    cantidad_unidades=0,
                )

    def test_descuento_porcentual_no_supera_100(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Presupuesto.objects.create(
                    cliente=self.cliente,
                    descuento_general_tipo=TipoDescuento.PORCENTAJE,
                    descuento_general_valor=Decimal("100.01"),
                )

    def test_descuento_monto_no_puede_ser_negativo(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Presupuesto.objects.create(
                    cliente=self.cliente,
                    descuento_general_tipo=TipoDescuento.MONTO,
                    descuento_general_valor=Decimal("-1"),
                )

    def _presupuesto(self):
        return Presupuesto.objects.create(cliente=self.cliente)

    def test_item_cantidad_debe_ser_positiva(self):
        presupuesto = self._presupuesto()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    descripcion_manual="Mano de obra",
                    cantidad=Decimal("0"),
                    precio_unitario=Decimal("100"),
                )

    def test_item_precio_no_puede_ser_negativo(self):
        presupuesto = self._presupuesto()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    descripcion_manual="Mano de obra",
                    precio_unitario=Decimal("-0.01"),
                )

    def test_item_costo_no_puede_ser_negativo(self):
        presupuesto = self._presupuesto()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    descripcion_manual="Mano de obra",
                    precio_unitario=Decimal("100"),
                    costo_unitario=Decimal("-0.01"),
                )

    def test_item_descuento_debe_estar_entre_0_y_100(self):
        presupuesto = self._presupuesto()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemPresupuesto.objects.create(
                    presupuesto=presupuesto,
                    descripcion_manual="Mano de obra",
                    precio_unitario=Decimal("100"),
                    descuento_pct=Decimal("100.01"),
                )


class ConstraintsComprasTrabajosStockTests(_DatosBaseMixin, TestCase):
    def test_linea_compra_cantidad_debe_ser_positiva(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LineaOrdenCompra.objects.create(
                    orden=orden,
                    producto_proveedor=self.producto_proveedor,
                    cantidad=Decimal("0"),
                    costo_esperado=Decimal("100"),
                )

    def test_linea_compra_costo_no_puede_ser_negativo(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LineaOrdenCompra.objects.create(
                    orden=orden,
                    producto_proveedor=self.producto_proveedor,
                    cantidad=Decimal("1"),
                    costo_esperado=Decimal("-0.01"),
                )

    def _trabajo(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        return Trabajo.objects.create(presupuesto=presupuesto)

    def test_duracion_etapa_debe_ser_positiva(self):
        trabajo = self._trabajo()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EtapaTrabajo.objects.create(
                    trabajo=trabajo,
                    titulo="Etapa inválida",
                    duracion_estimada_dias=0,
                )

    def test_material_necesario_debe_ser_positivo(self):
        trabajo = self._trabajo()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MaterialTrabajo.objects.create(
                    trabajo=trabajo,
                    descripcion_manual="Material manual",
                    cantidad_necesaria=Decimal("0"),
                )

    def test_ajuste_cero_se_rechaza_en_base(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MovimientoStock.objects.create(
                    producto=self.producto,
                    deposito=Deposito.GENERAL,
                    tipo=TipoMovimiento.AJUSTE,
                    cantidad=Decimal("0"),
                )

    def test_ajuste_cero_se_rechaza_desde_servicio(self):
        with self.assertRaisesMessage(ValueError, "no puede ser cero"):
            registrar_movimiento(
                producto=self.producto,
                deposito=Deposito.GENERAL,
                tipo=TipoMovimiento.AJUSTE,
                cantidad=Decimal("0"),
                usuario=None,
            )


class FormulariosNumericosTests(_DatosBaseMixin, TestCase):
    def test_presupuesto_form_rechaza_unidades_cero_y_descuento_mayor_100(self):
        form = PresupuestoForm(
            data={
                "cliente": self.cliente.pk,
                "direccion": "",
                "fecha_vencimiento": "",
                "cantidad_unidades": "0",
                "descuento_general_tipo": TipoDescuento.PORCENTAJE,
                "descuento_general_valor": "101",
                "notas_generales": "",
                "plantilla_condiciones": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cantidad_unidades", form.errors)
        self.assertIn("descuento_general_valor", form.errors)

    def test_item_form_rechaza_numeros_fuera_de_dominio(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        form = ItemManualForm(
            data={
                "seccion": "",
                "descripcion_manual": "Mano de obra",
                "cantidad": "0",
                "precio_unitario": "-1",
                "costo_unitario": "-1",
                "descuento_pct": "101",
                "tipo_iva": "incluido",
                "opcional": "",
                "incluido": "on",
            },
            presupuesto=presupuesto,
        )
        self.assertFalse(form.is_valid())
        for campo in ("cantidad", "precio_unitario", "costo_unitario", "descuento_pct"):
            self.assertIn(campo, form.errors)

    def test_linea_compra_form_rechaza_cantidad_cero_y_costo_negativo(self):
        orden = OrdenDeCompra.objects.create(
            proveedor=self.proveedor,
            deposito_destino=Deposito.GENERAL,
        )
        form = LineaOrdenCompraForm(
            data={
                "producto_proveedor": self.producto_proveedor.pk,
                "cantidad": "0",
                "costo_esperado": "-1",
            },
            orden=orden,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cantidad", form.errors)
        self.assertIn("costo_esperado", form.errors)

    def test_material_form_rechaza_cantidad_cero(self):
        trabajo = self._trabajo_para_form()
        form = MaterialManualForm(
            data={
                "etapa": "",
                "descripcion_manual": "Caño",
                "cantidad_necesaria": "0",
            },
            trabajo=trabajo,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cantidad_necesaria", form.errors)

    def _trabajo_para_form(self):
        presupuesto = Presupuesto.objects.create(cliente=self.cliente)
        return Trabajo.objects.create(presupuesto=presupuesto)

    def test_margen_form_rechaza_negativo(self):
        form = MargenMarcaForm(data={"margen": "-1"}, instance=self.marca)
        self.assertFalse(form.is_valid())
        self.assertIn("margen", form.errors)

    def test_stock_minimo_form_rechaza_negativo(self):
        form = StockMinimoForm(
            data={
                "stock_minimo_general": "-1",
                "stock_minimo_repuestos": "0",
            },
            instance=self.producto,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("stock_minimo_general", form.errors)
