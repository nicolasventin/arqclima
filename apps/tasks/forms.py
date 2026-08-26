from django import forms

from apps.accounts.models import User

from .models import Tarea


class TareaForm(forms.ModelForm):
    class Meta:
        model = Tarea
        fields = ["titulo", "descripcion", "asignado_a", "fecha_limite", "prioridad"]
        widgets = {
            "fecha_limite": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asignado_a"].queryset = User.objects.filter(is_active=True)
        self.fields["asignado_a"].required = True
        for name, field in self.fields.items():
            if name == "asignado_a" or name == "prioridad":
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs.setdefault("class", "form-control")
