from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("comercial/", views.ReporteComercialView.as_view(), name="comercial"),
]
