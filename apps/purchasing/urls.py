from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.OrdenListView.as_view(), name="lista"),
    path("nueva/", views.CrearOrdenView.as_view(), name="nueva"),
    path("<int:pk>/", views.OrdenDetailView.as_view(), name="detalle"),
    path("<int:pk>/lineas/agregar/", views.AgregarLineaView.as_view(), name="agregar_linea"),
    path("<int:pk>/productos/buscar/", views.BuscarProductosOrdenView.as_view(), name="buscar_productos"),
    path("lineas/<int:linea_pk>/eliminar/", views.EliminarLineaView.as_view(), name="eliminar_linea"),
    path("<int:pk>/emitir/", views.EmitirOrdenView.as_view(), name="emitir"),
    path("<int:pk>/reabrir/", views.ReabrirOrdenView.as_view(), name="reabrir"),
    path("<int:pk>/marcar-enviada/", views.MarcarEnviadaView.as_view(), name="marcar_enviada"),
    path("<int:pk>/cancelar/", views.CancelarOrdenView.as_view(), name="cancelar"),
    path("<int:pk>/cerrar/", views.CerrarOrdenView.as_view(), name="cerrar"),
    path("lineas/<int:linea_pk>/recibir/", views.RecibirLineaView.as_view(), name="recibir_linea"),
]
