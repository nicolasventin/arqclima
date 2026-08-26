from django.urls import path

from . import views

app_name = "clients"

urlpatterns = [
    path("", views.ClienteListView.as_view(), name="lista"),
    path("nuevo/", views.ClienteCreateView.as_view(), name="nuevo"),
    path("<int:pk>/editar/", views.ClienteUpdateView.as_view(), name="editar"),
]
