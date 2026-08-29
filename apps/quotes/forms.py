from decimal import Decimal

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from apps.catalog.models import ProductoProveedor
from apps.clients.models import Cliente

from .models import (
    ItemPresupuesto,
    LineaComercialPresupuesto,
    PlantillaCondiciones,
    Presupuesto,
    SeccionPresupuesto,
    TipoDescuento,
)


class PresupuestoForm(forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = [
            "cliente",
            "obra",
            "direccion",
            "referencia",
            "titulo_propuesta",
            "alcance_tecnico",
            "fecha_vencimiento",
            "cantidad_unidades",
            "importes_por_unidad",
            "mostrar_total_general",
            "descuento_general_tipo",
            "descuento_general_valor",
            "plantilla_condiciones",
            "notas_cliente",
            "forma_pago",
            "garantia",
            "exclusiones",
            "firma_texto",
            "notas_generales",
        ]
        widgets = {
            "cliente": forms.HiddenInput(),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "referencia": forms.Textarea(attrs={"rows": 3}),
            "alcance_tecnico": forms.Textarea(attrs={"rows": 7}),
            "notas_cliente": forms.Textarea(attrs={"rows": 5}),
            "forma_pago": forms.Textarea(attrs={"rows": 4}),
            "garantia": forms.Textarea(attrs={"rows": 4}),
            "exclusiones": forms.Textarea(attrs={"rows": 6}),
            "notas_generales": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in ("descuento_general_tipo", "plantilla_condiciones"):
                field.widget.attrs["class"] = "form-select"
            elif name in ("importes_por_unidad", "mostrar_total_general"):
                field.widget.attrs["class"] = "form-check-input"
            elif name != "cliente":
                field.widget.attrs.setdefault("class", "form-control")

        self.fields["cliente"].queryset = Cliente.objects.filter(activo=True)
        self.fields["cliente"].error_messages["required"] = "Seleccioná un cliente."

        self.fields["cantidad_unidades"].validators.append(MinValueValidator(1))
        self.fields["cantidad_unidades"].widget.attrs["min"] = "1"
        self.fields["descuento_general_valor"].validators.append(
            MinValueValidator(Decimal("0"))
        )
        self.fields["descuento_general_valor"].widget.attrs.update({"min": "0", "step": "0.01"})

        self.fields["plantilla_condiciones"].queryset = PlantillaCondiciones.objects.filter(activa=True)
        self.fields["plantilla_condiciones"].required = False
        self.fields["obra"].label = "Obra / proyecto"
        self.fields["direccion"].label = "Ubicación de la obra"
        self.fields["referencia"].label = "Referencia"
        self.fields["titulo_propuesta"].label = "Título de la propuesta"
        self.fields["alcance_tecnico"].label = "Alcance técnico general"
        self.fields["notas_cliente"].label = "Notas para el cliente"
        self.fields["forma_pago"].label = "Forma de pago"
        self.fields["garantia"].label = "Garantía"
        self.fields["exclusiones"].label = "Exclusiones"
        self.fields["firma_texto"].label = "Firma / responsable visible"
        self.fields["notas_generales"].label = "Notas internas"
        if not self.instance.pk:
            predeterminada = PlantillaCondiciones.objects.filter(
                activa=True, predeterminada=True
            ).first()
            if predeterminada:
                self.initial["plantilla_condiciones"] = predeterminada.pk

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("descuento_general_tipo")
        valor = cleaned.get("descuento_general_valor")
        if (
            tipo == TipoDescuento.PORCENTAJE
            and valor is not None
            and valor > Decimal("100")
        ):
            self.add_error(
                "descuento_general_valor",
                "El descuento porcentual no puede superar el 100%.",
            )
        return cleaned


class SeccionPresupuestoForm(forms.ModelForm):
    class Meta:
        model = SeccionPresupuesto
        fields = ["titulo", "descripcion_publica"]
        widgets = {
            "titulo": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ej. 1ERA ETAPA CALEFACCIÓN"}
            ),
            "descripcion_publica": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 7,
                    "placeholder": "Un punto técnico por línea. Ej.\nColector de bronce de 6 circuitos...\nCañería PEX 20 mm...",
                }
            ),
        }


class LineaComercialPresupuestoForm(forms.ModelForm):
    class Meta:
        model = LineaComercialPresupuesto
        fields = [
            "seccion",
            "etiqueta",
            "descripcion",
            "monto",
            "tipo_iva",
            "opcional",
            "incluido",
            "recomendado",
        ]
        widgets = {
            "seccion": forms.Select(attrs={"class": "form-select"}),
            "etiqueta": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ej. Materiales"}
            ),
            "descripcion": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Detalle opcional"}
            ),
            "monto": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "tipo_iva": forms.Select(attrs={"class": "form-select"}),
            "opcional": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "incluido": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "recomendado": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        if presupuesto is not None:
            self.fields["seccion"].queryset = presupuesto.secciones.all()
        self.fields["seccion"].required = False
        self.fields["monto"].validators.append(MinValueValidator(Decimal("0")))

    def clean(self):
        cleaned = super().clean()
        opcional = cleaned.get("opcional")
        incluido = cleaned.get("incluido")
        recomendado = cleaned.get("recomendado")
        if not opcional and incluido is False:
            self.add_error("incluido", "Un concepto obligatorio debe estar incluido.")
        if recomendado and not opcional:
            self.add_error("recomendado", "Solo un concepto opcional puede marcarse como recomendado.")
        return cleaned


def _estilar_campos(fields):
    for name, field in fields.items():
        if name in ("opcional", "incluido"):
            field.widget.attrs["class"] = "form-check-input"
        elif name in ("seccion", "producto_proveedor", "tipo_iva"):
            field.widget.attrs["class"] = "form-select form-select-sm"
        else:
            field.widget.attrs.setdefault("class", "form-control form-control-sm")

    fields["cantidad"].validators.append(MinValueValidator(Decimal("0.01")))
    fields["cantidad"].widget.attrs.update({"min": "0.01", "step": "0.01"})
    for nombre in ("precio_unitario", "costo_unitario"):
        fields[nombre].validators.append(MinValueValidator(Decimal("0")))
        fields[nombre].widget.attrs.update({"min": "0", "step": "0.01"})
    fields["descuento_pct"].validators.extend(
        [
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ]
    )
    fields["descuento_pct"].widget.attrs.update(
        {"min": "0", "max": "100", "step": "0.01"}
    )


class ItemCatalogoForm(forms.ModelForm):
    class Meta:
        model = ItemPresupuesto
        fields = [
            "seccion", "producto_proveedor", "cantidad", "precio_unitario",
            "costo_unitario", "descuento_pct", "tipo_iva", "opcional", "incluido",
        ]

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        _estilar_campos(self.fields)
        self.fields["producto_proveedor"].queryset = ProductoProveedor.objects.select_related(
            "producto", "producto__marca", "proveedor"
        ).filter(activo=True)
        if presupuesto is not None:
            self.fields["seccion"].queryset = presupuesto.secciones.all()
        self.fields["seccion"].required = False


class ItemManualForm(forms.ModelForm):
    class Meta:
        model = ItemPresupuesto
        fields = [
            "seccion", "descripcion_manual", "cantidad", "precio_unitario",
            "costo_unitario", "descuento_pct", "tipo_iva", "opcional", "incluido",
        ]

    def __init__(self, *args, presupuesto=None, **kwargs):
        super().__init__(*args, **kwargs)
        _estilar_campos(self.fields)
        self.fields["descripcion_manual"].required = True
        if presupuesto is not None:
            self.fields["seccion"].queryset = presupuesto.secciones.all()
        self.fields["seccion"].required = False
