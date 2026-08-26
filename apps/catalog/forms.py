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
