from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.StockListView.as_view(), name="lista"),
    path("movimientos/", views.MovimientoListView.as_view(), name="movimientos"),
    path("entrada/<str:deposito>/", views.RegistrarEntradaView.as_view(), name="entrada"),
    path("salida/<str:deposito>/", views.RegistrarSalidaView.as_view(), name="salida"),
    path("ajuste/", views.RegistrarAjusteView.as_view(), name="ajuste"),
    path(
        "pendientes-devolucion/",
        views.PendientesDevolucionView.as_view(),
        name="pendientes_devolucion",
    ),
    path(
        "pendientes-devolucion/<int:salida_pk>/devolver/",
        views.RegistrarDevolucionView.as_view(),
        name="registrar_devolucion",
    ),
    path(
        "productos/<int:pk>/stock-minimo/",
        views.ActualizarStockMinimoView.as_view(),
        name="actualizar_stock_minimo",
    ),
]
