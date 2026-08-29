from django import forms

from apps.catalog.models import Marca, Proveedor

from .models import ImportacionFila
from .parsing import ArchivoImportacionInvalido, MAX_ARCHIVO_BYTES, tipo_archivo_por_nombre


class NuevaImportacionForm(forms.Form):
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.filter(activo=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    archivo = forms.FileField(
        label="Archivo del proveedor",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".xlsx,.xls,.csv,.pdf,.docx,.jpg,.jpeg,.png,.webp",
            }
        ),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        try:
            tipo_archivo_por_nombre(archivo.name)
        except ArchivoImportacionInvalido as exc:
            raise forms.ValidationError(str(exc)) from exc
        if archivo.size > MAX_ARCHIVO_BYTES:
            raise forms.ValidationError(
                f"El archivo supera el máximo de {MAX_ARCHIVO_BYTES // (1024 * 1024)} MB."
            )
        if archivo.size == 0:
            raise forms.ValidationError("El archivo está vacío.")
        return archivo




class AsignarMarcaImportacionForm(forms.Form):
    marca = forms.ModelChoiceField(
        queryset=Marca.objects.all().order_by("nombre"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        label="Marca",
    )


class EditarFilaImportacionForm(forms.Form):
    marca = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    codigo = forms.CharField(
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    nombre = forms.CharField(
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    descripcion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    costo = forms.DecimalField(
        required=True,
        max_digits=12,
        decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
            }
        ),
    )
    codigo_proveedor = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    unidad = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ej. unidad, m, m2, kg, litro, caja, rollo, par, kit",
            }
        ),
    )
    categoria = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Solo se vincula si ya existe en ARQCLIMA",
            }
        ),
    )

    @classmethod
    def desde_fila(cls, fila: ImportacionFila):
        return cls(
            initial={
                "marca": fila.marca_texto,
                "codigo": fila.codigo,
                "nombre": fila.nombre_texto,
                "descripcion": fila.descripcion_texto,
                "costo": fila.costo,
                "codigo_proveedor": fila.codigo_proveedor_texto,
                "unidad": fila.unidad_texto,
                "categoria": fila.categoria_texto,
            }
        )

    def datos_para_reclasificar(self):
        return {
            "marca": self.cleaned_data["marca"],
            "codigo": self.cleaned_data["codigo"],
            "nombre": self.cleaned_data["nombre"],
            "descripcion": self.cleaned_data["descripcion"],
            "costo_crudo": self.cleaned_data["costo"],
            "codigo_proveedor": self.cleaned_data["codigo_proveedor"],
            "unidad": self.cleaned_data["unidad"],
            "categoria": self.cleaned_data["categoria"],
        }
