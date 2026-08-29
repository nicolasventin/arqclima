from django.urls import path

from . import views

app_name = "quotes"

urlpatterns = [
    path("", views.PresupuestoListView.as_view(), name="lista"),
    path("nuevo/", views.PresupuestoCreateView.as_view(), name="nuevo"),
    path("<int:pk>/", views.PresupuestoDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", views.PresupuestoUpdateView.as_view(), name="editar"),
    path("<int:pk>/pdf/", views.PresupuestoPDFView.as_view(), name="pdf"),
    path("<int:pk>/duplicar/", views.DuplicarPresupuestoView.as_view(), name="duplicar"),
    path("<int:pk>/enviar/", views.EnviarPresupuestoView.as_view(), name="enviar"),
    path("<int:pk>/aceptar/", views.AceptarPresupuestoView.as_view(), name="aceptar"),
    path("<int:pk>/rechazar/", views.RechazarPresupuestoView.as_view(), name="rechazar"),
    path("<int:pk>/cancelar/", views.CancelarPresupuestoView.as_view(), name="cancelar"),
    path("<int:pk>/reabrir/", views.ReabrirPresupuestoView.as_view(), name="reabrir"),
    path(
        "<int:pk>/revertir-aceptado/",
        views.RevertirAceptadoView.as_view(),
        name="revertir_aceptado",
    ),
    path("<int:pk>/secciones/agregar/", views.AgregarSeccionView.as_view(), name="agregar_seccion"),
    path(
        "secciones/<int:seccion_pk>/editar/",
        views.EditarSeccionView.as_view(),
        name="editar_seccion",
    ),
    path(
        "secciones/<int:seccion_pk>/eliminar/",
        views.EliminarSeccionView.as_view(),
        name="eliminar_seccion",
    ),
    path(
        "<int:pk>/lineas-comerciales/agregar/",
        views.AgregarLineaComercialView.as_view(),
        name="agregar_linea_comercial",
    ),
    path(
        "lineas-comerciales/<int:linea_pk>/eliminar/",
        views.EliminarLineaComercialView.as_view(),
        name="eliminar_linea_comercial",
    ),
    path(
        "<int:pk>/items/agregar-catalogo/",
        views.AgregarItemCatalogoView.as_view(),
        name="agregar_item_catalogo",
    ),
    path(
        "<int:pk>/items/agregar-manual/",
        views.AgregarItemManualView.as_view(),
        name="agregar_item_manual",
    ),
    path("items/<int:item_pk>/eliminar/", views.EliminarItemView.as_view(), name="eliminar_item"),
]
