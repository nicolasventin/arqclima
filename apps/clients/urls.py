from django.urls import path

from . import views

app_name = "clients"

urlpatterns = [
    path("", views.ClienteListView.as_view(), name="lista"),
    path("buscar/", views.ClienteSearchView.as_view(), name="buscar"),
    path("nuevo/", views.ClienteCreateView.as_view(), name="nuevo"),
    path("nuevo-rapido/", views.ClienteQuickCreateView.as_view(), name="nuevo_rapido"),
    path("<int:pk>/editar/", views.ClienteUpdateView.as_view(), name="editar"),
]
