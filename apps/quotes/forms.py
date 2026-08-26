from django import forms

from apps.catalog.models import ProductoProveedor

from .models import ItemPresupuesto, PlantillaCondiciones, Presupuesto, SeccionPresupuesto


class PresupuestoForm(forms.ModelForm):
    class Meta:
        model = Presupuesto
        fields = [
            "cliente", "direccion", "fecha_vencimiento", "cantidad_unidades",
            "descuento_general_tipo", "descuento_general_valor",
            "notas_generales", "plantilla_condiciones",
        ]
        widgets = {
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in ("cliente", "descuento_general_tipo", "plantilla_condiciones"):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs.setdefault("class", "form-control")

        self.fields["plantilla_condiciones"].queryset = PlantillaCondiciones.objects.filter(activa=True)
        self.fields["plantilla_condiciones"].required = False
        if not self.instance.pk:
            predeterminada = PlantillaCondiciones.objects.filter(
                activa=True, predeterminada=True
            ).first()
            if predeterminada:
                self.initial["plantilla_condiciones"] = predeterminada.pk


class SeccionPresupuestoForm(forms.ModelForm):
    class Meta:
        model = SeccionPresupuesto
        fields = ["titulo"]
        widgets = {
            "titulo": forms.TextInput(
                attrs={"class": "form-control form-control-sm", "placeholder": "Título de la sección"}
            ),
        }


def _estilar_campos(fields):
    for name, field in fields.items():
        if name in ("opcional", "incluido"):
            field.widget.attrs["class"] = "form-check-input"
        elif name in ("seccion", "producto_proveedor", "tipo_iva"):
            field.widget.attrs["class"] = "form-select form-select-sm"
        else:
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


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
