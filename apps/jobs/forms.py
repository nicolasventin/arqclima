from django import forms

from apps.accounts.models import User


class CrearTrabajoForm(forms.Form):
    tecnico_asignado = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        label="Técnico asignado (opcional, se puede definir después)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
