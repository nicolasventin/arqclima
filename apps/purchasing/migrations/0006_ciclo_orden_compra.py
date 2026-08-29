from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Sum


TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION purchasing_validar_transicion_orden()
RETURNS trigger AS $$
DECLARE
    tiene_lineas boolean;
    tiene_recepciones boolean;
    tiene_pendientes boolean;
BEGIN
    IF NEW.estado = OLD.estado THEN
        RETURN NEW;
    END IF;

    IF NOT (
        (OLD.estado = 'borrador' AND NEW.estado = 'pendiente_aprobacion')
        OR (OLD.estado = 'pendiente_aprobacion' AND NEW.estado IN ('aprobada', 'rechazada', 'borrador', 'cancelada'))
        OR (OLD.estado = 'rechazada' AND NEW.estado = 'borrador')
        OR (OLD.estado = 'aprobada' AND NEW.estado IN ('enviada', 'cancelada'))
        OR (OLD.estado = 'enviada' AND NEW.estado IN ('recepcion_parcial', 'recibida', 'cancelada'))
        OR (OLD.estado = 'recepcion_parcial' AND NEW.estado IN ('recibida', 'cerrada'))
        OR (OLD.estado = 'recibida' AND NEW.estado = 'cerrada')
    ) THEN
        RAISE EXCEPTION
            'Transición inválida de orden de compra: % -> %',
            OLD.estado, NEW.estado;
    END IF;

    SELECT EXISTS(
        SELECT 1 FROM purchasing_lineaordencompra
        WHERE orden_id = NEW.id
    ) INTO tiene_lineas;

    IF NEW.estado = 'pendiente_aprobacion' THEN
        IF NOT tiene_lineas THEN
            RAISE EXCEPTION 'No se puede enviar a aprobación una orden sin líneas';
        END IF;
        IF NEW.solicitud_aprobacion_en IS NULL OR NEW.solicitud_aprobacion_por_id IS NULL THEN
            RAISE EXCEPTION 'La solicitud de aprobación requiere usuario y fecha';
        END IF;
    END IF;

    IF NEW.estado = 'aprobada' THEN
        IF NOT tiene_lineas THEN
            RAISE EXCEPTION 'No se puede aprobar una orden sin líneas';
        END IF;
        IF NEW.aprobada_en IS NULL OR NEW.aprobada_por_id IS NULL THEN
            RAISE EXCEPTION 'La aprobación requiere usuario y fecha';
        END IF;
    END IF;

    IF NEW.estado = 'rechazada' THEN
        IF NEW.rechazada_en IS NULL OR NEW.rechazada_por_id IS NULL
           OR btrim(COALESCE(NEW.motivo_rechazo, '')) = '' THEN
            RAISE EXCEPTION 'El rechazo requiere usuario, fecha y motivo';
        END IF;
    END IF;

    IF NEW.estado = 'enviada' THEN
        IF NEW.enviada_en IS NULL OR NEW.enviada_por_id IS NULL THEN
            RAISE EXCEPTION 'Marcar enviada requiere usuario y fecha';
        END IF;
    END IF;

    SELECT EXISTS(
        SELECT 1
        FROM stock_movimientostock
        WHERE orden_compra_id = NEW.id
          AND linea_orden_compra_id IS NOT NULL
          AND tipo = 'entrada'
          AND cantidad > 0
    ) INTO tiene_recepciones;

    SELECT EXISTS(
        SELECT 1
        FROM purchasing_lineaordencompra l
        WHERE l.orden_id = NEW.id
          AND COALESCE(
                (
                    SELECT SUM(m.cantidad)
                    FROM stock_movimientostock m
                    WHERE m.linea_orden_compra_id = l.id
                      AND m.tipo = 'entrada'
                      AND m.cantidad > 0
                ),
                0
              ) < l.cantidad
    ) INTO tiene_pendientes;

    IF NEW.estado = 'recepcion_parcial' THEN
        IF NOT tiene_recepciones THEN
            RAISE EXCEPTION 'Recepción parcial requiere al menos una recepción real';
        END IF;
        IF NOT tiene_pendientes THEN
            RAISE EXCEPTION 'Una orden sin pendientes debe quedar como Recibida';
        END IF;
        IF NEW.primera_recepcion_en IS NULL THEN
            RAISE EXCEPTION 'Recepción parcial requiere fecha de primera recepción';
        END IF;
    END IF;

    IF NEW.estado = 'recibida' THEN
        IF NOT tiene_lineas OR NOT tiene_recepciones OR tiene_pendientes THEN
            RAISE EXCEPTION 'Una orden solo puede quedar Recibida si todas sus líneas fueron recibidas';
        END IF;
        IF NEW.primera_recepcion_en IS NULL OR NEW.recibida_en IS NULL THEN
            RAISE EXCEPTION 'Orden Recibida requiere fechas de recepción';
        END IF;
    END IF;

    IF NEW.estado = 'cancelada' THEN
        IF tiene_recepciones THEN
            RAISE EXCEPTION 'Una orden con recepciones no puede cancelarse; debe cerrarse';
        END IF;
        IF NEW.cancelada_en IS NULL OR NEW.cancelada_por_id IS NULL
           OR btrim(COALESCE(NEW.motivo_cancelacion, '')) = '' THEN
            RAISE EXCEPTION 'La cancelación requiere usuario, fecha y motivo';
        END IF;
    END IF;

    IF NEW.estado = 'cerrada' THEN
        IF NEW.cerrada_en IS NULL OR NEW.cerrada_por_id IS NULL THEN
            RAISE EXCEPTION 'El cierre requiere usuario y fecha';
        END IF;
        IF OLD.estado = 'recepcion_parcial'
           AND btrim(COALESCE(NEW.motivo_cierre, '')) = '' THEN
            RAISE EXCEPTION 'Cerrar una recepción parcial requiere motivo';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS purchasing_validar_transicion_orden_before_update
ON purchasing_ordendecompra;

CREATE TRIGGER purchasing_validar_transicion_orden_before_update
BEFORE UPDATE OF estado ON purchasing_ordendecompra
FOR EACH ROW
EXECUTE FUNCTION purchasing_validar_transicion_orden();
"""

REVERSE_TRIGGER_SQL = r"""
DROP TRIGGER IF EXISTS purchasing_validar_transicion_orden_before_update
ON purchasing_ordendecompra;
DROP FUNCTION IF EXISTS purchasing_validar_transicion_orden();
"""


def normalizar_recepciones_historicas(apps, schema_editor):
    Orden = apps.get_model("purchasing", "OrdenDeCompra")
    Linea = apps.get_model("purchasing", "LineaOrdenCompra")
    Movimiento = apps.get_model("stock", "MovimientoStock")

    ordenes = Orden.objects.filter(estado__in=["aprobada", "enviada"])
    for orden in ordenes.iterator():
        movimientos = Movimiento.objects.filter(
            orden_compra_id=orden.pk,
            linea_orden_compra_id__isnull=False,
            tipo="entrada",
            cantidad__gt=0,
        )
        if not movimientos.exists():
            continue

        lineas = list(Linea.objects.filter(orden_id=orden.pk))
        completa = bool(lineas)
        for linea in lineas:
            recibido = (
                Movimiento.objects.filter(
                    linea_orden_compra_id=linea.pk,
                    tipo="entrada",
                    cantidad__gt=0,
                ).aggregate(total=Sum("cantidad"))["total"]
                or 0
            )
            if recibido < linea.cantidad:
                completa = False
                break

        primera = movimientos.order_by("creado_en").values_list("creado_en", flat=True).first()
        ultima = movimientos.order_by("-creado_en").values_list("creado_en", flat=True).first()

        orden.primera_recepcion_en = primera
        if completa:
            orden.estado = "recibida"
            orden.recibida_en = ultima
            orden.save(update_fields=["estado", "primera_recepcion_en", "recibida_en"])
        else:
            orden.estado = "recepcion_parcial"
            orden.save(update_fields=["estado", "primera_recepcion_en"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0005_validaciones_numericas"),
        ("stock", "0008_control_stock_negativo"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="ordendecompra",
            name="estado",
            field=models.CharField(
                choices=[
                    ("borrador", "Borrador"),
                    ("pendiente_aprobacion", "Pendiente de aprobación"),
                    ("aprobada", "Aprobada"),
                    ("rechazada", "Rechazada"),
                    ("enviada", "Enviada"),
                    ("recepcion_parcial", "Recepción parcial"),
                    ("recibida", "Recibida"),
                    ("cerrada", "Cerrada"),
                    ("cancelada", "Cancelada"),
                ],
                default="borrador",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="solicitud_aprobacion_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="solicitud_aprobacion_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_solicitadas_aprobacion",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="aprobada_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="aprobada_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_aprobadas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="rechazada_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="rechazada_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_rechazadas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="motivo_rechazo",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="enviada_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="enviada_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_enviadas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="primera_recepcion_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="recibida_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="cerrada_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="cerrada_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_cerradas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="motivo_cierre",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="cancelada_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="cancelada_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_canceladas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="motivo_cancelacion",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(normalizar_recepciones_historicas, noop_reverse),
        migrations.RunSQL(TRIGGER_SQL, REVERSE_TRIGGER_SQL),
    ]
