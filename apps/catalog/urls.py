from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("productos/", views.ProductoListView.as_view(), name="producto_lista"),
    path("productos/nuevo/", views.ProductoCreateView.as_view(), name="producto_nuevo"),
    path("productos/<int:pk>/", views.ProductoDetailView.as_view(), name="producto_detalle"),
    path("productos/<int:pk>/editar/", views.ProductoUpdateView.as_view(), name="producto_editar"),
    path(
        "productos/<int:pk>/proveedores/agregar/",
        views.ProductoAgregarProveedorView.as_view(),
        name="producto_agregar_proveedor",
    ),
    path(
        "productos/<int:pk>/proveedores/<int:relacion_pk>/quitar/",
        views.ProductoQuitarProveedorView.as_view(),
        name="producto_quitar_proveedor",
    ),
    path("proveedores/", views.ProveedorListView.as_view(), name="proveedor_lista"),
    path("proveedores/nuevo/", views.ProveedorCreateView.as_view(), name="proveedor_nuevo"),
    path("proveedores/<int:pk>/editar/", views.ProveedorUpdateView.as_view(), name="proveedor_editar"),
]
