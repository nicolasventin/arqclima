from decimal import Decimal

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from apps.catalog.models import Categoria, Marca, Producto

from .models import ConfiguracionGeneral

MARGEN_WIDGET = forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"})
DIAS_WIDGET = forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "1", "min": "1"})


def _minimo_cero(field):
    field.validators.append(MinValueValidator(Decimal("0")))
    field.widget.attrs["min"] = "0"


def _minimo_uno(field):
    field.validators.append(MinValueValidator(1))
    field.widget.attrs["min"] = "1"


class RegistrarCostoForm(forms.Form):
    costo = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
    )


class MargenProductoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _minimo_cero(self.fields["margen"])

    class Meta:
        model = Producto
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class MargenMarcaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _minimo_cero(self.fields["margen"])

    class Meta:
        model = Marca
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class MargenCategoriaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _minimo_cero(self.fields["margen"])

    class Meta:
        model = Categoria
        fields = ["margen"]
        widgets = {"margen": MARGEN_WIDGET}


class ConfiguracionGeneralForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in (
            "margen_general",
            "margen_mano_obra",
            "flete_pct",
            "costo_financiero_pct",
            "margen_minimo_alerta",
        ):
            _minimo_cero(self.fields[nombre])
        _minimo_uno(self.fields["dias_seguimiento_presupuesto_enviado"])
        _minimo_uno(self.fields["dias_aviso_presupuesto_por_vencer"])

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
