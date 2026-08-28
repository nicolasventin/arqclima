from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("comercial/", views.ReporteComercialView.as_view(), name="comercial"),
    path("rentabilidad/", views.ReporteRentabilidadView.as_view(), name="rentabilidad"),
    path("stock/", views.ReporteStockView.as_view(), name="stock"),
    path("clientes/", views.ReporteClientesView.as_view(), name="clientes"),
    path("clientes/<int:pk>/", views.HistorialClienteView.as_view(), name="historial_cliente"),
    path("empleados/", views.ReporteEmpleadosView.as_view(), name="empleados"),
]
