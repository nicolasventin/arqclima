from decimal import Decimal

from django import forms
from django.core.validators import MinValueValidator

from apps.accounts.models import User
from apps.catalog.models import Producto

from .models import EtapaTrabajo, MaterialTrabajo


class CrearTrabajoForm(forms.Form):
    """
    El campo tecnico_asignado solo aparece si quien crea el trabajo
    tiene permiso para asignar técnico (Diego) — Rodrigo también puede
    crear un trabajo, pero asignar técnico es exclusivo de Diego (ver
    apps.jobs.permissions.puede_asignar_tecnico). Un trabajo creado
    por Rodrigo nace sin técnico; Diego lo asigna después desde el
    detalle del trabajo (AsignarTecnicoForm).
    """

    tecnico_asignado = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        label="Técnico asignado (opcional, se puede definir después)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, puede_asignar_tecnico=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not puede_asignar_tecnico:
            del self.fields["tecnico_asignado"]


class AsignarTecnicoForm(forms.Form):
    tecnico_asignado = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        label="Técnico asignado",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class CancelarTrabajoForm(forms.Form):
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Motivo obligatorio de la cancelación"}),
    )


class FinalizarTrabajoForm(forms.Form):
    observaciones = forms.CharField(
        required=False,
        label="Observaciones de cierre",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Observaciones finales (opcional)",
            }
        ),
    )


class EtapaTrabajoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duracion_estimada_dias"].validators.append(MinValueValidator(1))

    class Meta:
        model = EtapaTrabajo
        fields = ["titulo", "fecha_estimada", "duracion_estimada_dias"]
        widgets = {
            "titulo": forms.TextInput(attrs={"class": "form-control"}),
            "fecha_estimada": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "duracion_estimada_dias": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


class _MaterialFormBase(forms.ModelForm):
    def __init__(self, *args, trabajo=None, **kwargs):
        super().__init__(*args, **kwargs)
        if trabajo is not None:
            self.fields["etapa"].queryset = trabajo.etapas.all()
        self.fields["etapa"].required = False
        self.fields["etapa"].widget.attrs["class"] = "form-select"
        self.fields["cantidad_necesaria"].validators.append(
            MinValueValidator(Decimal("0.01"))
        )
        self.fields["cantidad_necesaria"].widget.attrs.update(
            {"class": "form-control", "min": "0.01", "step": "0.01"}
        )


class MaterialCatalogoForm(_MaterialFormBase):
    class Meta:
        model = MaterialTrabajo
        fields = ["etapa", "producto", "cantidad_necesaria"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(activo=True)
        self.fields["producto"].widget.attrs["class"] = "form-select"


class MaterialManualForm(_MaterialFormBase):
    class Meta:
        model = MaterialTrabajo
        fields = ["etapa", "descripcion_manual", "cantidad_necesaria"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["descripcion_manual"].required = True
        self.fields["descripcion_manual"].widget.attrs["class"] = "form-control"


class ActualizarCantidadMaterialForm(forms.ModelForm):
    class Meta:
        model = MaterialTrabajo
        fields = ["cantidad_necesaria"]
        widgets = {
            "cantidad_necesaria": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "step": "0.01",
                    "min": "0.01",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cantidad_necesaria"].validators.append(
            MinValueValidator(Decimal("0.01"))
        )


class RegistrarConsumoForm(forms.Form):
    """
    Regla de negocio 11: se precarga con lo enviado (se asume que se
    usó todo) — el técnico solo la edita hacia abajo si sobró algo. La
    vista convierte la diferencia en un registro de sobrante.
    """

    cantidad_usada = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
