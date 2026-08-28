from django import forms

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
    filtro de UX, no la garantía real (esa vive en el trigger de
    Postgres: ver apps.purchasing.models.LineaOrdenCompra).
    """

    class Meta:
        model = LineaOrdenCompra
        fields = ["producto_proveedor", "cantidad", "costo_esperado"]

    def __init__(self, *args, orden=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "producto_proveedor":
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs.setdefault("class", "form-control")
        if orden is not None:
            self.fields["producto_proveedor"].queryset = ProductoProveedor.objects.filter(
                proveedor=orden.proveedor, activo=True
            ).select_related("producto", "producto__marca")


class RecibirLineaForm(forms.Form):
    cantidad = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    costo_real = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
