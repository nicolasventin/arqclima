from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("", views.TareaListView.as_view(), name="lista"),
    path("mis-tareas/", views.MisTareasView.as_view(), name="mis_tareas"),
    path("nueva/", views.TareaCreateView.as_view(), name="nueva"),
    path("<int:pk>/editar/", views.TareaUpdateView.as_view(), name="editar"),
    path("<int:pk>/estado/", views.CambiarEstadoTareaView.as_view(), name="cambiar_estado"),
]
