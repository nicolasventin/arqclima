from django import forms

from .models import Cliente


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nombre", "tipo", "cuit_dni", "telefono", "email", "notas", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "activo":
                field.widget.attrs["class"] = "form-check-input"
            elif name == "tipo":
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs.setdefault("class", "form-control")


class ClienteRapidoForm(forms.ModelForm):
    """Alta mínima de cliente reutilizable desde flujos comerciales."""

    class Meta:
        model = Cliente
        fields = ["nombre", "tipo", "cuit_dni", "telefono", "email"]
        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Nombre o razón social",
                    "autocomplete": "organization",
                }
            ),
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "cuit_dni": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "CUIT o DNI",
                    "autocomplete": "off",
                }
            ),
            "telefono": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Teléfono",
                    "autocomplete": "tel",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "correo@empresa.com",
                    "autocomplete": "email",
                }
            ),
        }
