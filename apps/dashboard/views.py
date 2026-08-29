from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.clients.models import Cliente
from apps.core.permissions import get_user_role
from apps.jobs.models import EstadoTrabajo, Trabajo
from apps.purchasing.models import EstadoOrdenCompra, OrdenDeCompra
from apps.quotes.models import EstadoPresupuesto, Presupuesto
from apps.stock.models import Deposito
from apps.stock.services import productos_con_stock_bajo, salidas_repuestos_pendientes
from apps.tasks.models import EstadoTarea, Tarea
from apps.tasks.permissions import puede_gestionar_tareas


PERFILES_DASHBOARD = {
    "Administrador": {
        "eyebrow": "Dirección",
        "titulo": "Panel general",
        "descripcion": "Una vista rápida de comercial, operación, compras y stock.",
        "icono": "bi-speedometer2",
    },
    "Ventas y Presupuestos": {
        "eyebrow": "Comercial",
        "titulo": "Ventas y presupuestos",
        "descripcion": "Seguimiento comercial, presupuestos pendientes y próximos pasos.",
        "icono": "bi-file-earmark-bar-graph",
    },
    "Service y Repuestos": {
        "eyebrow": "Service",
        "titulo": "Service y repuestos",
        "descripcion": "Repuestos, devoluciones pendientes y alertas de stock.",
        "icono": "bi-wrench-adjustable-circle",
    },
    "Depósito": {
        "eyebrow": "Inventario",
        "titulo": "Depósito y compras",
        "descripcion": "Stock, recepciones y movimientos que requieren atención.",
        "icono": "bi-box-seam",
    },
    "Técnico de Campo": {
        "eyebrow": "Operación",
        "titulo": "Mis trabajos",
        "descripcion": "Trabajos asignados y tareas personales para resolver en campo.",
        "icono": "bi-tools",
    },
}


class HomeView(LoginRequiredMixin, TemplateView):
    """
    11B mantiene los widgets históricos de Etapa 9 para compatibilidad,
    pero agrega una capa explícita de dashboard por rol. Los datos
    siguen viniendo de los mismos modelos/servicios y cada métrica se
    gatea por permiso cuando corresponde.
    """

    template_name = "dashboard/home.html"

    def _metrica(self, *, label, value, hint, icon, url=None, tone="primary"):
        return {
            "label": label,
            "value": value,
            "hint": hint,
            "icon": icon,
            "url": url,
            "tone": tone,
        }

    def _accion(self, *, label, url, icon, style="primary"):
        return {
            "label": label,
            "url": url,
            "icon": icon,
            "style": style,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        rol = get_user_role(user)

        context["dashboard_rol"] = rol
        context["dashboard_perfil"] = PERFILES_DASHBOARD.get(
            rol,
            {
                "eyebrow": "ARQCLIMA",
                "titulo": "Mi panel",
                "descripcion": "Accesos y pendientes disponibles para tu usuario.",
                "icono": "bi-grid-1x2",
            },
        )
        context["dashboard_metricas"] = []
        context["dashboard_acciones"] = []

        context["puede_gestionar_permisos"] = user.has_perm("accounts.manage_permissions")
        context["mis_tareas"] = (
            Tarea.objects.filter(asignado_a=user)
            .exclude(estado=EstadoTarea.COMPLETADA)
            .order_by("fecha_limite")[:5]
        )
        context["puede_gestionar_tareas"] = puede_gestionar_tareas(user)

        tareas_vencidas_propias = Tarea.objects.filter(
            asignado_a=user,
            fecha_limite__lt=timezone.localdate(),
        ).exclude(estado=EstadoTarea.COMPLETADA).count()
        context["tareas_vencidas_propias"] = tareas_vencidas_propias

        stock_bajo = None
        if user.has_perm("reports.view_reporte_stock"):
            stock_bajo = productos_con_stock_bajo()
            context["stock_bajo"] = stock_bajo[:5]
            context["stock_bajo_total"] = len(stock_bajo)

        if user.has_perm("reports.view_reporte_comercial"):
            context["presupuestos_pendientes"] = Presupuesto.objects.filter(
                estado=EstadoPresupuesto.ENVIADO
            ).count()

        if user.has_perm("jobs.view_trabajo"):
            trabajos_propios = (
                Trabajo.objects.filter(tecnico_asignado=user)
                .exclude(estado__in=[EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO])
                .select_related("presupuesto__cliente")
                .order_by("-creado_en")
            )
            context["mis_trabajos_activos"] = trabajos_propios.count()
            context["mis_trabajos_recientes"] = trabajos_propios[:5]

        metricas = context["dashboard_metricas"]
        acciones = context["dashboard_acciones"]

        if rol == "Administrador":
            if user.has_perm("purchasing.approve_ordendecompra"):
                pendientes_aprobacion = OrdenDeCompra.objects.filter(
                    estado=EstadoOrdenCompra.PENDIENTE_APROBACION
                ).count()
                metricas.append(
                    self._metrica(
                        label="Compras por aprobar",
                        value=pendientes_aprobacion,
                        hint="Órdenes esperando decisión",
                        icon="bi-cart-check",
                        url=f"{reverse('purchasing:lista')}?estado={EstadoOrdenCompra.PENDIENTE_APROBACION}",
                        tone="warning",
                    )
                )
            if user.has_perm("jobs.view_trabajo"):
                activos = Trabajo.objects.exclude(
                    estado__in=[EstadoTrabajo.TERMINADO, EstadoTrabajo.CANCELADO]
                ).count()
                metricas.append(
                    self._metrica(
                        label="Trabajos activos",
                        value=activos,
                        hint="En cualquier etapa operativa",
                        icon="bi-tools",
                        url=reverse("jobs:lista"),
                    )
                )
            if stock_bajo is not None:
                metricas.append(
                    self._metrica(
                        label="Alertas de stock",
                        value=len(stock_bajo),
                        hint="Producto/depósito bajo mínimo",
                        icon="bi-exclamation-triangle",
                        url=reverse("stock:lista"),
                        tone="danger" if stock_bajo else "success",
                    )
                )
            if context.get("presupuestos_pendientes") is not None:
                metricas.append(
                    self._metrica(
                        label="Presupuestos pendientes",
                        value=context["presupuestos_pendientes"],
                        hint="Enviados sin resolver",
                        icon="bi-file-earmark-text",
                        url=f"{reverse('quotes:lista')}?estado={EstadoPresupuesto.ENVIADO}",
                    )
                )
            acciones.extend(
                [
                    self._accion(
                        label="Ver reportes",
                        url=reverse("reports:comercial"),
                        icon="bi-bar-chart-line",
                    ),
                    self._accion(
                        label="Usuarios y permisos",
                        url=reverse("accounts:permisos"),
                        icon="bi-shield-lock",
                        style="outline",
                    ),
                ]
            )

        elif rol == "Ventas y Presupuestos":
            pendientes = context.get("presupuestos_pendientes")
            if pendientes is not None:
                metricas.append(
                    self._metrica(
                        label="Esperando respuesta",
                        value=pendientes,
                        hint="Presupuestos enviados",
                        icon="bi-send",
                        url=f"{reverse('quotes:lista')}?estado={EstadoPresupuesto.ENVIADO}",
                        tone="warning" if pendientes else "success",
                    )
                )
            aceptados_sin_trabajo = Presupuesto.objects.filter(
                estado=EstadoPresupuesto.ACEPTADO,
                trabajo__isnull=True,
            ).count()
            metricas.append(
                self._metrica(
                    label="Aceptados por iniciar",
                    value=aceptados_sin_trabajo,
                    hint="Todavía sin trabajo creado",
                    icon="bi-clipboard-check",
                    url=f"{reverse('quotes:lista')}?estado={EstadoPresupuesto.ACEPTADO}",
                )
            )
            if user.has_perm("clients.view_cliente"):
                metricas.append(
                    self._metrica(
                        label="Clientes activos",
                        value=Cliente.objects.filter(activo=True).count(),
                        hint="Base comercial vigente",
                        icon="bi-people",
                        url=reverse("clients:lista"),
                    )
                )
            if user.has_perm("quotes.add_presupuesto"):
                acciones.append(
                    self._accion(
                        label="Nuevo presupuesto",
                        url=reverse("quotes:nuevo"),
                        icon="bi-plus-circle",
                    )
                )
            if user.has_perm("clients.add_cliente"):
                acciones.append(
                    self._accion(
                        label="Nuevo cliente",
                        url=reverse("clients:nuevo"),
                        icon="bi-person-plus",
                        style="outline",
                    )
                )

        elif rol == "Service y Repuestos":
            if stock_bajo is not None:
                bajos_repuestos = [
                    fila for fila in stock_bajo if fila[1] == Deposito.REPUESTOS
                ]
                metricas.append(
                    self._metrica(
                        label="Repuestos bajo mínimo",
                        value=len(bajos_repuestos),
                        hint="Alertas del depósito de service",
                        icon="bi-boxes",
                        url=reverse("stock:lista"),
                        tone="danger" if bajos_repuestos else "success",
                    )
                )
            devoluciones = len(salidas_repuestos_pendientes())
            metricas.append(
                self._metrica(
                    label="Devoluciones pendientes",
                    value=devoluciones,
                    hint="Repuestos que deben volver",
                    icon="bi-arrow-return-left",
                    url=reverse("stock:pendientes_devolucion"),
                    tone="warning" if devoluciones else "success",
                )
            )
            metricas.append(
                self._metrica(
                    label="Mis tareas",
                    value=len(context["mis_tareas"]),
                    hint="Pendientes o en proceso",
                    icon="bi-check2-square",
                    url=reverse("tasks:mis_tareas"),
                )
            )
            acciones.append(
                self._accion(
                    label="Ver stock de repuestos",
                    url=reverse("stock:lista"),
                    icon="bi-box-seam",
                )
            )

        elif rol == "Depósito":
            if stock_bajo is not None:
                metricas.append(
                    self._metrica(
                        label="Alertas de stock",
                        value=len(stock_bajo),
                        hint="Bajo mínimo configurado",
                        icon="bi-exclamation-triangle",
                        url=reverse("stock:lista"),
                        tone="danger" if stock_bajo else "success",
                    )
                )
            por_recibir = OrdenDeCompra.objects.filter(
                estado__in=[
                    EstadoOrdenCompra.ENVIADA,
                    EstadoOrdenCompra.RECEPCION_PARCIAL,
                ]
            ).count()
            metricas.append(
                self._metrica(
                    label="Compras por recibir",
                    value=por_recibir,
                    hint="Enviadas o con recepción parcial",
                    icon="bi-truck",
                    url=reverse("purchasing:lista"),
                    tone="warning" if por_recibir else "success",
                )
            )
            metricas.append(
                self._metrica(
                    label="Mis tareas",
                    value=len(context["mis_tareas"]),
                    hint="Pendientes o en proceso",
                    icon="bi-check2-square",
                    url=reverse("tasks:mis_tareas"),
                )
            )
            acciones.extend(
                [
                    self._accion(
                        label="Registrar entrada",
                        url=reverse("stock:entrada", args=[Deposito.GENERAL]),
                        icon="bi-box-arrow-in-down",
                    ),
                    self._accion(
                        label="Ver movimientos",
                        url=reverse("stock:movimientos"),
                        icon="bi-clock-history",
                        style="outline",
                    ),
                ]
            )

        elif rol == "Técnico de Campo":
            metricas.append(
                self._metrica(
                    label="Trabajos activos",
                    value=context.get("mis_trabajos_activos", 0),
                    hint="Asignados a vos",
                    icon="bi-tools",
                    url=reverse("jobs:lista"),
                )
            )
            metricas.append(
                self._metrica(
                    label="Tareas pendientes",
                    value=len(context["mis_tareas"]),
                    hint="Pendientes o en proceso",
                    icon="bi-check2-square",
                    url=reverse("tasks:mis_tareas"),
                )
            )
            metricas.append(
                self._metrica(
                    label="Tareas vencidas",
                    value=tareas_vencidas_propias,
                    hint="Requieren atención",
                    icon="bi-exclamation-circle",
                    url=reverse("tasks:mis_tareas"),
                    tone="danger" if tareas_vencidas_propias else "success",
                )
            )
            acciones.extend(
                [
                    self._accion(
                        label="Mis trabajos",
                        url=reverse("jobs:lista"),
                        icon="bi-tools",
                    ),
                    self._accion(
                        label="Mis tareas",
                        url=reverse("tasks:mis_tareas"),
                        icon="bi-check2-square",
                        style="outline",
                    ),
                ]
            )

        else:
            metricas.append(
                self._metrica(
                    label="Mis tareas",
                    value=len(context["mis_tareas"]),
                    hint="Pendientes o en proceso",
                    icon="bi-check2-square",
                    url=reverse("tasks:mis_tareas"),
                )
            )

        return context
