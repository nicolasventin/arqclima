from decimal import Decimal

from django import forms
from django.core.validators import MinValueValidator

from apps.catalog.models import Producto

from .models import Deposito, MovimientoStock

CAMPO_CANTIDAD = forms.DecimalField(
    max_digits=12, decimal_places=2, min_value=0.01,
    widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
)


class ForzadoStockNegativoMixin:
    def __init__(self, *args, permitir_forzado=False, **kwargs):
        super().__init__(*args, **kwargs)
        if permitir_forzado:
            self.fields["forzar_stock_negativo"] = forms.BooleanField(
                required=False,
                label=(
                    "Forzar salida aunque el stock quede negativo "
                    "(acción excepcional y auditada)"
                ),
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            self.fields["motivo_forzado"] = forms.CharField(
                required=False,
                label="Motivo de la salida forzada",
                widget=forms.Textarea(
                    attrs={
                        "class": "form-control",
                        "rows": 2,
                        "placeholder": "Explique por qué se autoriza dejar stock negativo.",
                    }
                ),
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("forzar_stock_negativo") and not (
            cleaned.get("motivo_forzado") or ""
        ).strip():
            self.add_error(
                "motivo_forzado",
                "Debe indicar el motivo para forzar una salida con stock insuficiente.",
            )
        return cleaned


class EntradaSalidaForm(ForzadoStockNegativoMixin, forms.Form):
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


class AjusteForm(ForzadoStockNegativoMixin, forms.Form):
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ("stock_minimo_general", "stock_minimo_repuestos"):
            self.fields[nombre].validators.append(MinValueValidator(Decimal("0")))
            self.fields[nombre].widget.attrs["min"] = "0"

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
