from decimal import Decimal

from django import forms
from django.core.validators import MinValueValidator

from apps.catalog.models import ProductoProveedor, Proveedor
from apps.stock.models import Deposito

from .models import LineaOrdenCompra


class CrearOrdenForm(forms.Form):
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.filter(activo=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    deposito_destino = forms.ChoiceField(
        choices=Deposito.choices, widget=forms.Select(attrs={"class": "form-select"})
    )
    notas = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2})
    )


class LineaOrdenCompraForm(forms.ModelForm):
    """
    producto_proveedor queda acotado a los del proveedor de la orden —
    filtro de UX y validación del formulario, no la garantía final (esa
    vive también en el trigger de Postgres).
    """

    class Meta:
        model = LineaOrdenCompra
        fields = ["producto_proveedor", "cantidad", "costo_esperado"]
        widgets = {
            "producto_proveedor": forms.HiddenInput(),
        }

    def __init__(self, *args, orden=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cantidad"].widget.attrs.setdefault("class", "form-control")
        self.fields["costo_esperado"].widget.attrs.setdefault("class", "form-control")
        self.fields["cantidad"].validators.append(MinValueValidator(Decimal("0.01")))
        self.fields["cantidad"].widget.attrs.update({"min": "0.01", "step": "0.01"})
        self.fields["costo_esperado"].validators.append(MinValueValidator(Decimal("0")))
        self.fields["costo_esperado"].widget.attrs.update({"min": "0", "step": "0.01"})

        qs = ProductoProveedor.objects.none()
        if orden is not None:
            qs = ProductoProveedor.objects.filter(
                proveedor=orden.proveedor,
                activo=True,
                producto__activo=True,
            ).select_related("producto", "producto__marca")
        self.fields["producto_proveedor"].queryset = qs
        self.fields["producto_proveedor"].error_messages["required"] = "Seleccioná un producto."


class RecibirLineaForm(forms.Form):
    cantidad = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    costo_real = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
