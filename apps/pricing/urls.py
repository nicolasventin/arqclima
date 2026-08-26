from django.urls import path

from . import views

app_name = "pricing"

urlpatterns = [
    path(
        "productos/<int:producto_pk>/proveedores/<int:relacion_pk>/costos/nuevo/",
        views.RegistrarCostoView.as_view(),
        name="registrar_costo",
    ),
    path(
        "productos/<int:pk>/margen/",
        views.ActualizarMargenProductoView.as_view(),
        name="actualizar_margen_producto",
    ),
    path("configuracion/", views.ConfiguracionPreciosView.as_view(), name="configuracion"),
    path(
        "configuracion/general/",
        views.ActualizarConfiguracionGeneralView.as_view(),
        name="actualizar_configuracion_general",
    ),
    path(
        "configuracion/categorias/<int:pk>/",
        views.ActualizarMargenCategoriaView.as_view(),
        name="actualizar_margen_categoria",
    ),
    path(
        "configuracion/marcas/<int:pk>/",
        views.ActualizarMargenMarcaView.as_view(),
        name="actualizar_margen_marca",
    ),
]
