from django import forms

from apps.catalog.models import Categoria, Marca, Producto

from .models import ConfiguracionGeneral

MARGEN_WIDGET = forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"})
DIAS_WIDGET = forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "1", "min": "1"})


class RegistrarCostoForm(forms.Form):
    costo = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
    )


class MargenProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class MargenMarcaForm(forms.ModelForm):
    class Meta:
        model = Marca
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class MargenCategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class ConfiguracionGeneralForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionGeneral
        fields = [
            "margen_general", "margen_mano_obra",
            "flete_pct", "costo_financiero_pct", "margen_minimo_alerta",
            "dias_seguimiento_presupuesto_enviado", "dias_aviso_presupuesto_por_vencer",
        ]
        widgets = {
            "margen_general": MARGEN_WIDGET,
            "margen_mano_obra": MARGEN_WIDGET,
            "flete_pct": MARGEN_WIDGET,
            "costo_financiero_pct": MARGEN_WIDGET,
            "margen_minimo_alerta": MARGEN_WIDGET,
            "dias_seguimiento_presupuesto_enviado": DIAS_WIDGET,
            "dias_aviso_presupuesto_por_vencer": DIAS_WIDGET,
        }
