from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("comercial/", views.ReporteComercialView.as_view(), name="comercial"),
    path("rentabilidad/", views.ReporteRentabilidadView.as_view(), name="rentabilidad"),
    path("stock/", views.ReporteStockView.as_view(), name="stock"),
]
