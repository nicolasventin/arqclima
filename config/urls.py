from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("catalogo/", include("apps.catalog.urls")),
    path("precios/", include("apps.pricing.urls")),
    path("importaciones/", include("apps.imports.urls")),
    path("clientes/", include("apps.clients.urls")),
    path("presupuestos/", include("apps.quotes.urls")),
    path("tareas/", include("apps.tasks.urls")),
    path("stock/", include("apps.stock.urls")),
    path("", include("apps.dashboard.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
