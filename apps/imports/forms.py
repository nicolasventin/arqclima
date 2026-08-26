from django import forms

from apps.catalog.models import Proveedor


class NuevaImportacionForm(forms.Form):
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.filter(activo=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    archivo = forms.FileField(
        label="Archivo Excel (.xlsx)",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".xlsx"}),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Por ahora solo se aceptan archivos .xlsx (Excel).")
        return archivo
