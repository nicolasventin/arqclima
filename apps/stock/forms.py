from django import forms

from apps.catalog.models import Producto

from .models import Deposito, MovimientoStock

CAMPO_CANTIDAD = forms.DecimalField(
    max_digits=12, decimal_places=2, min_value=0.01,
    widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
)


class EntradaSalidaForm(forms.Form):
    """
    Cantidad siempre se carga positiva acá — el servicio la guarda con
    el signo que corresponda según sea entrada o salida. Evita que el
    usuario tenga que acordarse de poner "-3" para una salida.
    """

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(activo=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    cantidad = CAMPO_CANTIDAD
    referencia_libre = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Obra Casa Pérez, venta de mostrador..."}),
    )

    def __init__(self, *args, solo_repuestos=False, **kwargs):
        super().__init__(*args, **kwargs)
        if solo_repuestos:
            self.fields["producto"].queryset = self.fields["producto"].queryset.filter(
                es_repuesto=True
            )


class SalidaRepuestosForm(EntradaSalidaForm):
    requiere_devolucion = forms.BooleanField(
        required=False,
        label="Pendiente de devolución (material para service, hay que rendir cuentas)",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class AjusteForm(forms.Form):
    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(activo=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    cantidad = forms.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Con signo: positivo suma, negativo resta.",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    referencia_libre = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Motivo del ajuste"}),
    )

    def clean_cantidad(self):
        cantidad = self.cleaned_data["cantidad"]
        if cantidad == 0:
            raise forms.ValidationError("El ajuste no puede ser cero.")
        return cantidad


class DevolucionForm(forms.Form):
    cantidad = CAMPO_CANTIDAD


class StockMinimoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["stock_minimo_general", "stock_minimo_repuestos"]
        widgets = {
            "stock_minimo_general": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "step": "0.01"}
            ),
            "stock_minimo_repuestos": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "step": "0.01"}
            ),
        }
