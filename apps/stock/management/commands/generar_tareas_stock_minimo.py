from django.core.management.base import BaseCommand

from apps.accounts import roles
from apps.accounts.models import User
from apps.stock.models import Deposito
from apps.stock.services import productos_con_stock_bajo
from apps.tasks.models import EstadoTarea, Tarea, TipoAutomatizacion
from apps.tasks.services import crear_tarea_automatica


class Command(BaseCommand):
    """
    Regla de negocio 17: genera una tarea de reposición por cada
    producto+depósito por debajo de su stock mínimo configurado
    (apps.stock.services.productos_con_stock_bajo, el mismo criterio
    que ya usaba la alerta visual de la pantalla de stock).

    A quién se asigna: Repuestos → Gabriel (Service y Repuestos),
    controla esa línea desde la Etapa 2 y hace sus propias órdenes de
    compra de repuestos (Etapa 7/8). General → Diego, no existe un rol
    "Compras" separado en el equipo y es quien configura los umbrales
    de stock mínimo. Si el rol correspondiente no tiene ningún usuario
    cargado, la tarea se crea sin asignar (asignado_a admite null,
    igual que el resto del proyecto) en vez de fallar.

    Idempotente por (producto, deposito): no duplica mientras ya haya
    una tarea de este tipo sin completar para ese par puntual — una
    vez marcada Completada, un nuevo faltante sí genera una tarea
    nueva (a diferencia de las automatizaciones de Presupuesto, acá
    "resuelto y vuelve a pasar" es un evento nuevo, no el mismo).
    Pensado para cron diario, sin scheduler propio todavía.
    """

    help = "Genera tareas de reposición para productos por debajo de su stock mínimo."

    def handle(self, *args, **options):
        total = 0
        for producto, deposito, cantidad_actual in productos_con_stock_bajo():
            ya_existe = (
                Tarea.objects.filter(
                    generada_por=TipoAutomatizacion.STOCK_MINIMO,
                    producto=producto,
                    deposito=deposito,
                )
                .exclude(estado=EstadoTarea.COMPLETADA)
                .exists()
            )
            if ya_existe:
                continue

            nombre_rol = roles.SERVICE_Y_REPUESTOS if deposito == Deposito.REPUESTOS else roles.ADMINISTRADOR
            asignado_a = User.objects.filter(groups__name=nombre_rol).first()

            crear_tarea_automatica(
                TipoAutomatizacion.STOCK_MINIMO,
                titulo=f"Reponer stock: {producto}",
                descripcion=(
                    f"{producto} está en {cantidad_actual} unidades en "
                    f"{Deposito(deposito).label}, por debajo del mínimo configurado."
                ),
                asignado_a=asignado_a,
                producto=producto,
                deposito=deposito,
            )
            total += 1

        self.stdout.write(self.style.SUCCESS(f"{total} tarea(s) de reposición generada(s)."))
