from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportacionListView.as_view(), name="lista"),
    path("nueva/", views.NuevaImportacionView.as_view(), name="nueva"),
    path("<int:pk>/", views.ImportacionDetailView.as_view(), name="detalle"),
    path("<int:pk>/archivo/", views.ArchivoImportacionView.as_view(), name="archivo"),
    path(
        "<int:pk>/imagenes/<int:imagen_pk>/",
        views.ImagenImportacionView.as_view(),
        name="imagen",
    ),
    path(
        "<int:pk>/asignar-marca/",
        views.AsignarMarcaImportacionView.as_view(),
        name="asignar_marca",
    ),
    path(
        "<int:pk>/filas/<int:fila_pk>/editar/",
        views.EditarFilaImportacionView.as_view(),
        name="fila_editar",
    ),
    path("<int:pk>/confirmar/", views.ConfirmarImportacionView.as_view(), name="confirmar"),
    path("<int:pk>/descartar/", views.DescartarImportacionView.as_view(), name="descartar"),
]
