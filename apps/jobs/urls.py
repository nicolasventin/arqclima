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
]
