from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportacionListView.as_view(), name="lista"),
    path("nueva/", views.NuevaImportacionView.as_view(), name="nueva"),
    path("<int:pk>/", views.ImportacionDetailView.as_view(), name="detalle"),
    path("<int:pk>/confirmar/", views.ConfirmarImportacionView.as_view(), name="confirmar"),
    path("<int:pk>/descartar/", views.DescartarImportacionView.as_view(), name="descartar"),
]
