from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.TrabajoListView.as_view(), name="lista"),
    path("<int:pk>/", views.TrabajoDetailView.as_view(), name="detalle"),
    path(
        "crear/<int:presupuesto_pk>/",
        views.CrearTrabajoView.as_view(),
        name="crear",
    ),
    path("<int:pk>/estado/", views.CambiarEstadoTrabajoView.as_view(), name="cambiar_estado"),
    path("<int:pk>/tecnico/", views.AsignarTecnicoView.as_view(), name="asignar_tecnico"),
    path("<int:pk>/cancelar/", views.CancelarTrabajoView.as_view(), name="cancelar"),
    path(
        "<int:pk>/materiales/generar/",
        views.GenerarListadoMaterialesView.as_view(),
        name="generar_materiales",
    ),
    path("<int:pk>/etapas/agregar/", views.AgregarEtapaView.as_view(), name="agregar_etapa"),
    path("etapas/<int:etapa_pk>/eliminar/", views.EliminarEtapaView.as_view(), name="eliminar_etapa"),
    path(
        "<int:pk>/materiales/agregar-catalogo/",
        views.AgregarMaterialCatalogoView.as_view(),
        name="agregar_material_catalogo",
    ),
    path(
        "<int:pk>/materiales/agregar-manual/",
        views.AgregarMaterialManualView.as_view(),
        name="agregar_material_manual",
    ),
    path(
        "materiales/<int:material_pk>/cantidad/",
        views.ActualizarCantidadMaterialView.as_view(),
        name="actualizar_cantidad_material",
    ),
    path(
        "materiales/<int:material_pk>/eliminar/",
        views.EliminarMaterialView.as_view(),
        name="eliminar_material",
    ),
]
