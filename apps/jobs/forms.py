from django import forms

from apps.accounts.models import User


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
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Motivo de la cancelación"}),
    )
