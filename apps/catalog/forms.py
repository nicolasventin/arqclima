from django import forms

from .models import Producto, ProductoProveedor, Proveedor


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = [
            "marca", "codigo", "nombre", "descripcion", "categoria",
            "unidad_medida", "es_repuesto", "activo", "notas",
        ]

    def __init__(self, *args, forzar_repuesto=False, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["es_repuesto"].widget.attrs["class"] = "form-check-input"
        self.fields["activo"].widget.attrs["class"] = "form-check-input"

        if forzar_repuesto:
            # El usuario solo tiene manage_repuestos (no change_producto
            # general): no puede sacar un producto de la línea de
            # repuestos. disabled=True hace que Django ignore cualquier
            # valor que llegue por POST y use siempre el initial.
            self.fields["es_repuesto"].disabled = True
            self.fields["es_repuesto"].help_text = (
                "Los productos que gestionás siempre quedan en la línea de repuestos."
            )
            self.initial["es_repuesto"] = True


class ProveedorForm(forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = [
            "nombre_comercial", "razon_social", "cuit",
            "contacto_nombre", "telefono", "email", "notas", "activo",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "activo":
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs.setdefault("class", "form-control")


class ProveedorRapidoForm(forms.ModelForm):
    """
    Alta mínima reutilizable desde flujos que necesitan un proveedor sin
    abandonar la tarea actual (por ejemplo, importar una lista de precios).
    El proveedor nace activo por default del modelo.
    """

    class Meta:
        model = Proveedor
        fields = [
            "nombre_comercial",
            "razon_social",
            "cuit",
            "contacto_nombre",
            "telefono",
            "email",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_nombre_comercial(self):
        nombre = (self.cleaned_data.get("nombre_comercial") or "").strip()
        if Proveedor.objects.filter(nombre_comercial__iexact=nombre).exists():
            raise forms.ValidationError("Ya existe un proveedor con ese nombre.")
        return nombre

    def clean_cuit(self):
        cuit = (self.cleaned_data.get("cuit") or "").strip()
        if cuit and Proveedor.objects.filter(cuit__iexact=cuit).exists():
            raise forms.ValidationError("Ya existe un proveedor con ese CUIT.")
        return cuit


class ProductoProveedorForm(forms.ModelForm):
    class Meta:
        model = ProductoProveedor
        fields = ["proveedor", "codigo_proveedor", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].widget.attrs["class"] = "form-select"
        self.fields["codigo_proveedor"].widget.attrs["class"] = "form-control"
        self.fields["activo"].widget.attrs["class"] = "form-check-input"
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
